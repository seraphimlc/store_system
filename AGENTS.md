# 巡店结算系统 — Agent 冷启动手册（精简版）

> 先读本文件 + `docs/索引.md`（模块速查）；改完代码同步更新这两个文件。
> 详细设计/流程/坑 → `docs/产品设计-系统逻辑全览.md`、`docs/结算主流程设计-v3.md`。

## 一句话
FastAPI + SQLAlchemy 2 + Alembic + Jinja2 + htmx：巡店文件 → 判定 → 申诉/绩效确认 → 正式表 → 绩效工资(两期发薪) → 月度对账(归因+AI) → 薪资找平(找平执行/上月余量递延)。本地 SQLite；线上 PG(docker)。URL 无 /v3（旧 /v3/* 为别名）。

## 常用命令（cwd=项目根）
```bash
./.venv/bin/python -m pytest tests_web/test_xxx.py -q        # 单测（小改只跑相关）
./.venv/bin/python -m pytest tests tests_web -q              # 全量
./scripts/dev_server.sh                                       # 本地服务(自动加载 .env 含 AI key)
DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/xxx.py
mcp_service/.venv/bin/python -m pytest mcp_service/tests -q  # MCP 服务测试（独立 3.12 venv）
scripts/mcp_restart.sh                                       # 本机 MCP 服务（HTTP，重启+验证工具清单+自洽检查）
DATABASE_URL="sqlite:///file:$PWD/store_settle_live.db?mode=ro&uri=true" \
  mcp_service/.venv/bin/python mcp_service/server.py --stdio   # stdio 模式（WorkBuddy 直接拉起，无需端口）
```
- 账号：`admin/demo123`；员工 `demo123`。**heredoc `python3 <<EOF` 偶发静默失败 → 一律写 scripts/*.py 文件执行**。
- AI：本地 .env（AI_API_KEY/AI_BASE_URL/AI_MODEL=deepseek-v4-flash）；线上 deploy/.env。ai_chat.chat 自动重试 2 次。
- **MCP 服务（WorkBuddy 接入）**：独立进程 `mcp_service/`，**16 个场景化工具（员工 3 个 / 管理员 13 个，
  含写类走闸门；按身份裁剪列表）**；旧工具名已删（2026-09-26 重构，规格 `docs/specs-mcp-tools-scenario.md`，
  注册唯一入口 `mcp_service/scenario_ops.py`，能力函数仍在 `*_ops.py`），
  **不动主 venv（3.9.6）**；**改完代码必须重启**：`scripts/mcp_restart.sh`（一条命令：重启 + 等就绪 +
  验证工具清单（=16）+ 跑数据自洽检查）；依赖 `requirements-mcp.txt`（`mcp==2.2.0` 需 Python ≥3.10，生产 3.11 可用）。启动前 **DATABASE_URL 必须显式设置**（`app/config.py` 的 `get_settings()` 带 @lru_cache，`app.db` import 时固化缓存，不设会静默连到空库）。设计/计划见 `docs/索引.md §1.5`。
- **MCP OAuth（SSO，2026-09-26）**：`/.well-known/oauth-*` + `/oauth/*` 挂在 web 应用
  （`app/routers/oauth_r.py` + `app/services/oauth.py`）；员工点「连接」→ 浏览器登录 → 自动授权，
  换出的 access token 即 `api_tokens` 一行（授权矩阵/审计/状态联动全继承）。`mcp_service/auth.py`
  的 401 头带 `resource_metadata`（客户端据此发起授权）。配置：`VISIT_OAUTH_ISSUER` /
  `VISIT_OAUTH_ENABLED`（0 关）/ `VISIT_OAUTH_ACCESS_HOURS` / `VISIT_OAUTH_REFRESH_DAYS`。
  验收测试：`tests_web/test_oauth.py`（含真实 MCP 服务子进程端到端）。
- **接入其它系统时看 `docs/MCP对接手册.md`**（可复用：架构选型/协议实测事实/OAuth 五个致命坑/
  工具设计/身份权限/部署发布/验收清单/工程实践）——本系统的具体实现见 `docs/索引.md §1.5`。

## 关键基准（**系统实测**，非手工文件）
> ⚠️ 2026-09-25 用户明确：**手工基准文件（巡回最终结算/8月成绩/闫总最终）已作废删除**，
> 不再是验收依据。以下是**系统自身**跑出的数字（8/9 月由真实文件上传产生，可复现）。

| 项 | 2026-08 | 2026-09 |
|---|---|---|
| 正式表（店数） | **12507** | **15070** |
| 1点/2点 | 8199 / 4308 | 10663 / 4407 |
| 总点数 | **16787** | **19477** |
| 工资（円） | 4,895,750 | 5,173,000 |
| 员工 | 34 | 34 |

- 口径随上传文件与规则变化；**验收以"与同一份库的 SQL 直查一致"为准**，不钉固定数字。
- 7 月为部分数据（缺 0701-0715），暂不作为基准。

- **工资规则**：每点 250円（按月可配 per_point），奖金**每满门槛点奖 3000**（整月滚动、不跨月；门槛按月可配：默认 68，`BONUS_GROUP_SCHEDULE` 如 `2026-09=75`）；2点成功率 37% 仅展示。
- **判重与点数（9月起固化口径）**：窗口=结算月；键=(店名trim,月)；
  组内**锚点优先级**：① deploy=YES 的行（该店当月有投放→**2点**，多条 YES 取最早）
  ② 非 AUDIT_FAILED 行（SUCCESS/OTHER→**1点**）③ 纯 AUDIT_FAILED 无投放→**0点=不计成绩，不入正式表**；
  同级内取 modified 最早；跨月不互压。（原"与手工《巡回最终结算》逐人一致"的依据已于
  2026-09-25 随手工基准文件作废，不再引用。）
- **申诉**：master_late/from_sub 可申诉，其余滤除不可申诉；默认全部认可；存在 pending 申诉的文件不能入正式表。
- **对账**：person_daily_stats 为本地侧；recon_day_rows 只存问题行；同月重传→旧任务标「上一版」、新任务当前；person_points(人月汇总)对账写全量 ReconDataRow。
- **找平（金额制·自动）**：偏差金额 = 对账金额 − 系统已发（**负=扣款/正=补款**，含奖金），**自动写表**（`adjust_amount=diff_amount`，无需点击、页面只读）；发薪时**上半月扣/补 → 不够转下半月 → 两期都不够递延下月**（链式 `prev_adjust_amount`：本月结转 = diff + 本月两期吸收上月结转后的剩余）。8 月封账数据不随规则变更。
- **员工可见起始月（2026-10 启用）**：员工端只显示 ≥ `staff_visible_from` 的月份，管理员不受限。
  配置：SysConfig 表 `staff_visible_from`（/config 页可改），未配置回退 env `STAFF_VISIBLE_FROM`（默认 `2026-10`）。
  过滤点：`/my/perf` 月份下拉 + 直链隐藏月回退到可见最新月/空态（`month=""` 时提前返回，不读全量数据防泄漏）。
- **H5 响应式（2026-10 交付）**：顶栏 ≤860px 折叠为汉堡菜单（Alpine `menu` 开关）；宽表包 `.tbl-wrap`（overflow-x 滚动），≤760px 首列 sticky 吸附；统计卡 `auto-fit,minmax`；登录卡 `width:min(360px,92vw)`；窄屏表单/输入 max-width:100%。
- **多语言（中文/日本語，2026-10 交付）**：模板用全局函数 `t('中文')` 翻译，字典在 `app/i18n.py`（key=中文原文，ja=日文；未收录回退原文）。
  语言解析（`app/main.py lang_middleware`）：`?lang=` → cookie → 账号级 `User.lang`（员工管理页可指定 zh/ja/空=自动）→ 浏览器 Accept-Language → 默认 zh。
  切换：顶栏「中文/日本語」（`lang_url` 保留 query）；仅显式 `?lang=` 才写 cookie（避免默认语言覆盖账号级）。
  注意：模板循环变量**不可用 `t`**（会覆盖全局 t()，如 recon 用 `task`、dashboard 用 `tp`）；`{# 注释 #}` 与 JS 内中文不会被脚本包裹。
  批量包裹工具：`scripts/i18n_wrap.py`（自动包文本节点+placeholder/title，跳过 script/style；用后需人工查破损：`{#` 注释、字符串字面量内嵌套）。

## 员工每日填报 + 对比 + AI 报告（2026-09 交付 · 独立支线）
- **员工每天报一次**：担当区域 + **1点店铺数 + 2点店铺数**（`/my/report`）。**一天一条**，重复提交被拒；
  **不拍照、不定位、不涉及店名**；填报数据**不参与工资计算**，只用于与文件结果对比。
- **管理端**（`/staff-reports`）：填报列表（区间/人筛选、分页）→ **对比页**
  （逐人合计 + **准确率排名** + 逐日明细 + 三类计数）→ **一键生成 AI 分析报告** → Excel 双导出。
- **口径（写死在测试里）**：`Δ = 系统 − 自报`（**正=少报、负=多报**）；
  **准确率 = 1 − Σ|Δ| ÷ Σ系统**（用绝对值之和，**多报少报不抵消**）；
  **漏填报不计入准确率**，单独算「应填未填」= **系统当天有数据但员工没报**的天数；
  两侧都没数据的身份不进报告。
- **报告双视角**：管理端看全员 + 「建议核实清单」；**员工端 `/my/report/feedback` 只看自己**，
  且**只给数字**（准确率 + 逐日 Δ，2026-09-28 用户明确"评语给管理员就行"）——
  数据来自物化表 `staff_report_compare_person/day`（读接口只读，服务端按 `person_code` 过滤，
  payload 从不进模板）。
- **报告双语**：管理端中文、员工端日文（`VISIT_REPORT_LANGS=zh,ja`）；prompt 里的**标签按语言本地化**
  （日文用 システム／自己申告／正確率／要申告未申告），否则日文报告会混中文词。
- **数字一律由程序算，模型只写评语**（prompt 明确"只使用给出的数字"）；
  同区间 + 同数据指纹（**含 `PROMPT_VERSION`**）→ 复用已有报告，不重复烧 token；
  单一语言失败不影响另一种，全部失败 → `failed` 但**对比数字照常可用**；`VISIT_REPORT_AI=0` 一键关闭。
- **管理员补录/修改自报（2026-09-28）**：`/staff-reports` 提供「补录 / 修改自报」卡片
  （员工 + 日期 + 区域 + 1点/2点），已存在的填报会被覆盖（`source=admin`）。
  **锁定规则**：某日期 ≤ 已导入正式数据的最后一天 = **已对账 → 自报不可改**；
  之后的日期仍可补录/修改（`daily_report.is_locked/coverage_end/save_by_admin`）。
  员工端「当天可改」也守同一条规则（今天已对账则拒绝）。列表页锁定行显示「已对账」、可改行给「改」入口。
- **删除能力（2026-09-28）**：管理端列表对**未对账**的行给「删除」（与补录同一条锁定规则，
  已对账不可删）；对比页报告卡可「删除报告」（删除分析 + 物化行，报告可再生）。
  清理演示/测试数据用 `scripts/cleanup_staff_reports.py`（默认 dry-run，`--apply --force` 可越过锁定）。
- **单日准确率**（规格 §7 `acc_day = max(0, 1 − |Δ总| ÷ max(系统总店数,1))`，只算 both 日）：
  员工端与管理端逐日表都显示「准确率」列；物化表 `staff_report_compare_day.acc`。
- **漏填汇总**（规格 §7 漏填标记）：管理端列表按人给出 应填天数/实填天数/漏填天数 + 漏填日期。
- 数据指纹**含逐人逐日 Δ**：只看合计的话，"同样合计换个日子" 会误判为同数据而复用旧报告。
- Excel 导出用 `Workbook(write_only=True)` **流式写**（不在内存里保留整份工作簿）。
- **分数与比例（2026-10-02 用户要求，纯展示、不落库）**：自报只存 1点/2点店数，
  **分数（点数）= 1点×1 + 2点×2**（同结算侧 `perf.py` 的 `points = p1 + p2*2`）、
  **2点分数占比 = 2点分数 ÷ 总分数 = (2点店数×2) ÷ (1点店数 + 2点店数×2)**
  （用户 2026-10-02 明确"我要 2 点分数占比"；⚠️ **与看板 `p2rate`（2点店数占比）不是同一个数**：
  1点6家/2点2家 → 分数占比 40%，店数占比 25%）。算法唯一来源 `report_compare.p2_score_share()`，
  一律**现算**：
  - 员工端 `/my/report`：**自动算 6 个数**（2026-10-02 用户要求）——**1点分数 / 2点分数 / 总分数 /
    总店铺数 / 2点分数占比 / 2点店铺占比**；填报表单**实时算**（Alpine 计算属性，
    `data-testid="live-p1pts|live-p2pts|live-points|live-stores|live-rate|live-store-rate"`）、
    已报回显、历史表（逐日 + 本月合计）都有；`daily_report.month_days()` 逐日与合计都带这 6 个字段；
  - 管理端 `/staff-reports` 列表：新增「分数（点数）」「2点分数占比」两列；
    **顶部是「自报汇总」模块**（`data-testid="report-summary"`，卡片 + 小表；2026-10-02 用户先后要求
    "太丑了别只显示一行"→"做成一个正经的模块"→"两个占比加一列叫 2 点占比"）：
    `店铺数`（1点/2点/合计）与`分数（点数）`（1点分/2点分/总分）两行 × `1点/2点/合计/2点占比` 四列，
    2点占比按行给（店铺数行=2点店÷总店、分数行=2点分数÷总分数），表头带 tooltip 说明；
    右侧小字是 `区间 · 第 x/y 页 · 自报条数`；数值由 `reports_summary()` 一次聚合查询给出、**不受分页影响**；
    对比页 `/staff-reports/compare` 逐人新增「系统分数」「自报分数」（比例在同格 hint 里），
    逐日在「系统/自报」格子的 hint 里带分数与比例；
  - **导出**：自报明细加「分数(点数)」「2点分数占比%」；对比导出（按人/逐日）加「系统分数/自报分数/占比」。
  - 实现位置：`report_compare.points_of()/p2_score_share()`（唯一算法来源）、`_row_dict()`、`compare()`、
    `reports_summary()`、
    `daily_report.month_days()`、`report_export.py`。测试 `test_points_and_rate_helpers` 等 4 项。
- **员工下拉一律带搜索过滤（2026-10-02 用户要求）**：`app/static/emp_select.js`（原生 JS，无依赖，
  由 `base.html` 全局引入）——给 `<select data-emp-filter>` 自动挂一个筛选框：
  **输编号后 5 位（选项文本里含完整编号，子串即命中）或姓名任一个字**，查询串做 **NFKC 归一**
  （全角数字/字母、大小写都能匹配）；**唯一命中时自动选中并派发 `change`**（看板那种"change 即加载"的下拉
  直接就出结果）；命中多个保留当前选择、由人工挑；无命中输入框标红提示「无匹配」；
  **已选项永远保留在列表里**（避免过滤动作悄悄改掉已提交的筛选条件）；htmx 局部替换后自动重挂。
  已挂的 4 处：`/staff-reports`（筛选 + 补录卡片）、`/staff-reports/compare` 筛选、`/dashboard` 员工维度。
  新增员工下拉时**记得加 `data-emp-filter`**；测试 `test_employee_selects_have_filter`。
- **员工管理页不再展示 MCP Token（2026-10-02 用户："这块没有用，隐藏掉"）**：
  整列（前缀/scope/状态/代发/吊销）已从 `/staff-admin` 移除，页面也不再查 token（省 N 次查询）；
  **端点 `/{uid}/tokens/issue|revoke` 与 `mcp_service` 全部保留**（脚本/测试仍可用），
  一次性"新 Token 已生成"提示也保留（只有直接 POST 该端点才会出现）。
  自报页的**「漏填汇总」卡片同日一并去掉**（服务层 `report_compare.missing_summary()` 保留，MCP 可调用）。
- **员工管理页编辑方式（2026-10-01 用户要求）**：`/staff-admin` 每行只留一个「编辑」按钮 →
  打开**原生 `<dialog>` 弹窗**（不依赖脚本库），里面改**姓名 / 登录名 / 状态 / 界面语言 / 重置口令**
  （`POST /staff-admin/{uid}/edit`，服务层 `staff_accounts.update_staff`）。
  行内的"改状态/设语言/重置口令"表单已撤掉；旧端点 `/{uid}/status`、`/{uid}/lang`、`/{uid}/reset`
  保留（测试与脚本仍可用）。登录名重复 → 明确拒绝（**不自动加后缀**）；改名会同步 `persons.display_name`。
  ⚠️ **人员编号不支持修改**（用户 2026-10-01 决定："太重了，先别做"）：编号是身份键，
  改它要级联 `persons` + 近 20 张引用表（计划/自报/绩效/工资/分析/对账），风险大于收益；
  弹窗里编号是**只读**展示。真要做时的实现思路与风险清单见本条（级联写法：插新 Person → 改子表 → 删旧行）。
- **手工建号（编号即身份键）**：`/staff-admin` 新增「新建员工」——**编号必填**（NFKC 归一）、
  重复只提示不覆盖；导入时**按编号判定**（有→用系统里的，无→创建），命中手工建号的人时补写
  `first_seen_import_id`。**不做身份合并**：不同编号 = 不同的人（用户明确）。
- 表：`staff_daily_reports`（`(person_code, report_date)` 唯一）/ `staff_report_analyses`（`summary` 数字 + `payload` 评语）；
  另有物化表 `staff_report_compare_person`（人×区间）/ `staff_report_compare_day`（人×日）、
  防重复提交的 `form_tokens`；迁移 `b8c9d0e1f2a3` → `c9d0e1f2a3b4` → `d0e1f2a3b4c5` → `a2b3c4d5e6f7`（正式表日期索引）→ `b3c4d5e6f7a8`（逐日单日准确率列）；**现有业务表一行未改**（只加表/列/索引）。
- **看板 AI 分析（公司 + 每人）的样本门槛（2026-10-01 用户口径）**：入口 `/dashboard`（公司分析走
  `/dashboard/analysis`、每人走 `/dashboard/staff`），存 `staff_analyses`
  （`COMPANY/ALL` = 公司；`编号/YYYY-MM` = 每人，**按月**）。
  生成时机：上传入表后（`flow.py` 后台）+ 点「算工资」后（`payroll_settle_generate` 后台线程）
  + 打开某人模块时懒生成；公司分析在打开看板时按需生成并缓存（数据指纹变了才重算）。
  **门槛 = 当期有数据 且 有效店合计 ≥5**（`staff_sample_ok`）——**不再要求"至少 2 个月"**
  （用户："自报数据只是一个分析项，没有自报数据也能分析出来"）；没有上月数据时 prompt 不做环比、
  只跟全公司比。历史月份**不会自动补**（只按当月生成），需要时手动补跑。
- **报告生成结点 = 文件入表后自动**（`flow.auto_finalize_pipeline` → `report_ai.auto_for_import`）：
  自报在时间上先于系统数据，文件入表完成才是两边齐备的时刻；同数据指纹复用、AI 未配置/文件无正式记录则跳过。
- **物化与失效**：报告生成时把对比结果落物化表（员工端只读，避免实时重算与并发写）；
  **数据一变就刷新**——员工填报/修改（`daily_report._refresh_materialized`）与月度统计重算
  （`perf.refresh_compare_materialized`）都会重算覆盖该日期的报告。读接口**绝不写库**。
- **防重复提交（2026-09-28 全站机制）**：服务端**一次性提交令牌**（`app/forms.py` + `app/services/form_tokens.py`）——
  每个 POST 表单渲染时发一个 token，提交时校验并**立即作废**（2 小时 TTL、绑定本人、机器端点与未登录豁免），
  客户端再加"提交按钮禁用"兜底。**新增表单记得放 `{{ form_token() }}`**。
- **迁移必须真跑**：`tests_web/test_migrations.py` 会在临时库执行 `alembic upgrade head` 并核对与 ORM 的列/可空性是否一致
  （本地用 `create_all` 建表，只跑测试不跑迁移曾漏掉一个致命迁移错误）。
- 测试：`tests_web/test_daily_report.py`（填报/逐日/趋势/对比口径/导出）、
  `tests_web/test_report_robustness.py`（物化失效/状态机/重试护栏/可见月/编号归一/导出白名单）、
  `tests_web/test_form_token.py`（一次性令牌）、`tests_web/test_migrations.py`（迁移冒烟 + schema 一致性）；
  AI 全程 mock 不连外网。i18n 巡检：`scripts/i18n_audit.py`（缺日文/死键）、`scripts/i18n_prune.py`（清死键）。

## 日期计划（半月出勤登记，2026-10 交付 · 分支 feat/date-plan）
- **需求**：员工在每个半月开始时登记未来半个月的出勤（每月 **3 号 / 18 号前**），管理员据此分配任务；
  **计划只是预报，实际出勤以自报为准**。规格 `docs/specs-date-plan.md`。
- **半月口径（用户确认）**：自然半月 `1–15`（H1，截止 3 号）/ `16–月末`（H2，截止 18 号）；
  H2 天数 13–16 随月份变，**不写死 15**；H1/H2 不跨月。
- **填报窗口（2026-10-01 用户补充）**：**提前 7 天开放 → 到截止日为止**。`period_window(key)` =
  `(期首 − 7 天, 截止日)` → H1 = 上月 **24 号**~本月 3 号（不是 25 号！），H2 = 本月 9 号~18 号。
  窗口外提交 → `WindowClosed`（没开/已关）；`open_period` 同一时刻最多一期（窗口互不重叠），
  4–8 号、19–23 号是**间隙**（没有可填报期 → 不催办、登录直进自报页）；
  `default_period` = 正在填报的那一期，否则本期（管理员默认看"未来两周"）。
- **格状态（唯一来源 `date_plan.cell_state`）优先级**：**□ 已出勤（已自报）> 过去看事实（×）>
  计划值（○ / ×）> 默认出勤（浅色 ○）> – 未登记**。
  员工端对"可改且未登记"的日子按"**默认每天都出勤**"显示 ○；管理端显示 –（**未登记 ≠ 可出勤**，
  否则管理员会把没登记的人当成能派活）；**默认出勤的 ○ 是浅色**（`.c.assumed`），与本人登记的实色 ○ 区分。
- **数据**：`staff_date_plans`（`(person_code, plan_date)` 唯一，`available=True` 默认）；只落"不出勤"的例外，
  其余按默认落库；**重复提交 = 覆盖**；**不存 period_key**（半月归属由 plan_date 现算，规则改了历史不歧义）。
- **锁定**：窗口内 `< 今天` 与**已有自报**的日期不可改；保存时**跳过锁定日期**（不新增、不改写），
  整期都锁 → `AllLocked`。
  ⚠️ 与每日填报的锁定规则**不同**（自报锁在"已对账"，计划锁在"已过去/已自报"），别互相套用。
- **单表设计（2026-10-01 用户口径："避免关联查询"、"自报了就直接改计划表里的状态"）**：
  `staff_date_plans` 一行 = 一人一天，**格状态全在这一行里**：
  `available`（计划值）+ **`reported`（该日已自报，□）** → `cell_state()` 直接算出来。
  - **自报写透** `date_plan.mark_reported(db, code, date, flag)`：由 `app/services/daily_report.py`
    的 **4 个写入口**调用（员工提交/当天修改/管理员补录/删除自报）；有计划行只改 `reported`
    （保留 `available`，删自报能回原值），没行就插一行 `source='report'`（**不算"已登记"**）。
  - **渲染路径绝不查 `staff_daily_reports`**（矩阵/员工页/导出都只读计划表）。
    守门测试：`test_admin_matrix_reads_only_plan_table`。修复工具：`date_plan.rebuild_reported()`。
  - `is_submitted` = 该期存在 `source != 'report'` 的行（只自报没登记的人仍会被催办）。
  - ⚠️ 反范式的代价：绕过 `daily_report` 直改自报表不会同步 `reported`（历史脚本/手工 SQL 要跑 rebuild）。
- **过去的日子看事实（2026-10-01 用户口径）**：`d < 今天` → **有自报 □ / 没自报 ×**，
  **计划值不参与显示**（用户例子：今天 10-04，10-03 计划出勤但没自报 → ×）。
  过去**没有 `–` 这一态**，`none_cnt`（未登记人数）只统计今天及以后；员工端同口径
  （行内写"未自报（按不出勤）"）。
- **"今天"这一列仍显示计划值**（2026-10-01 用户明确选"保持现状"）：当天不按事实，**次日 0 点起**才变 ×。
  依据：线上实测自报集中在 **JST 16:16–22:54** 提交，当天上午若按事实会整列 ×、被误读成"今天没人来"。
  锁定测试 `test_today_keeps_plan_until_next_day`（不要改成"当天即事实"）。
- **员工端交互（2026-10-01 用户口径）**：半月**顺序列表**（一天一条，从上到下），**默认可出勤**，
  **点行内任何位置切换"可出勤 ⇄ 不出勤"**，点完保存。版式（用户："不太好看呢" 后改的）：
  左**状态色条**（绿/红）+ 日期星期 + 右侧**开关组件**（开=可出勤·绿滑块靠右 / 关=不出勤·红滑块靠左，
  文字随状态变，"做成一个开关组件"是用户 2026-10-01 的要求）；
  **"今天"不打标记**；顶部只留窗口状态条 + 一行摘要（不再堆图例/长句）。
  **纯 CSS 实现**（表单区不用 Alpine，CDN 挂了也能填）：`<label class="pickrow">` 包整行
  + 隐藏 checkbox（仍在表单里，带 `role="switch"`）+ 兄弟选择器驱动滑块与文字。
- **新入职员工（2026-10-01 用户口径："不用关心入职日，就以填报当天为入职日"）**：
  **名册起点** `roster_start_map()` = 账号创建日 / 人员记录创建日 / **首次计划日** 三者取最早（JST，不人工维护）；
  **入职前的格子留空**（`STATE_NA="na"`，不计 计划出勤/实际出勤/未登记 任何统计；以前显示成 × = 把没入职算旷工）；
  **他没填时按"可出勤"算**（`unfilled_default` 返回 `on`，浅色 ○）；
  **新人可补登当期**（`personal_window_state()`：起点晚于该期窗口关闭日 → 该期对他开放到期末，
  `save_plan` 与 `needs_plan` 都认 → 登录就催填）；老员工照旧 `WindowClosed`。
  员工页状态条「你是本期新入职的…」，默认期 = 本期（`default_period_for()`）。
  测试：`test_roster_start_takes_earliest_appearance` / `test_matrix_blanks_days_before_roster_start` /
  `test_new_hire_can_backfill_current_period` / `test_employee_page_late_join_banner_and_na_rows` /
  `test_matrix_marks_before_start_blank_in_export`。
- **不在职（停用/离职）员工（2026-10-01 用户口径）**：不在职 = 有账号但 `can_login=False`
  （`resigned`/`disabled`）。**本期一条数据都没有 → 不进矩阵**（过滤掉）；**有数据 → 过去按事实（□/×）、
  今天及以后一律 ×**（`cell_state(..., inactive=True)`），且**不计入「计划出勤」/「未登记」**；
  行名淡色 + tooltip「停用/离职：今天及以后按不出勤」。
  另：整行都落在"入职前（`na`）"的人（这一期与他无关）也不显示。
  测试 `test_matrix_hides_inactive_without_period_data` / `test_inactive_employee_future_days_all_off` /
  `test_matrix_hides_rows_entirely_before_roster_start`。
- **管理端** `/staff-plans`：行=员工（在岗/请假 ∪ 本期有登记记录的人；停用离职但登记过的不隐去）× 列=日期。
  **只有两列文字（员工、日期）——没有「登记」列、不罗列未提交人名**（2026-10-01 用户："看不动"）；
  **员工编号只显示后 5 位**（`date_plan.short_code`，2026-10-01 用户："员工编号取后5位就行"），
  完整编号挂在单元格 `title` 上；**页面与导出同一口径**；
  未提交靠"整行没有实色标记"体现。
  **顶部只有两行**（2026-10-01 用户："顶部的几个模块太丑了，还都是废话"）：信息条 `.plan-bar`
  （`填报期 X~Y · 未提交计划 N/M · 今天 N 人可出勤`）+ 一行图例；**已删 4 个统计卡片与 3 段说明文字**
  （原 `window-hint`/`past-rule`/`default-hint` 三个 testid 不再存在，测试反向断言它们不在）。
  底部**三个按日统计行**（2026-10-01 用户要求）：**计划出勤**（`plan_cnt`：计划标了可出勤的格子
  + 窗口已关未登记按默认出勤的格子；原「可出勤人数（今天及以后）」改名，去掉"今天及以后"限制，
  过去的日子也给出计划值，便于与"实际出勤"对比）、**实际出勤**（`actual_cnt`：当天有自报的格子数，
  未来日期留空）、**未登记人数**；「其中默认出勤（未登记）」作为计划出勤的子行照旧保留。
- **导出照着"排班计划"参考表（`万总/排班计划.png`）排，去掉区域标识行**：
  `说明行 → 标题行（期间·填报期）→ 表头（姓名/员工编号/可出动天数/MM/DD…）→ 七曜行 → 数据 → 按日小计`；
  **没有登记列**，未登记且未默认出勤的格子**留空**；`freeze_panes="D3"`。
- **窗口关了还没填 → "未填默认"（2026-10-01 用户口径，同日改过一次）**：
  最初是"默认全部出勤"，用户改成"**老员工漏填 → 默认全部不出勤**"（理由："可能要离职了；
  正常的员工都是会填报的"），同时保留"**本期新入职 → 默认可出勤**"（他还能补填，见下一条）。
  实现：`unfilled_default(key, today, roster_start)` → `""` / `"on"` / `"off"`；
  生效区间 = **今天及以后 + 已过截止日**（`assumed_default` 作底层日期判定）；
  **过去不追认**、**登记过的人不受影响**（默认优先级最低）；整期已过去的历史半月不启用。
  显示：`on` → **浅色 ○**、`off` → **浅色 ×**（都和"亲手填的"区分得开）。
  统计：**「计划出勤」不含默认不出勤的老员工**；**「未登记人数」= 那天还没填计划的人数**
  （今天及以后，含按默认算的）→"谁没填"的信号永远在，管理员据此催办。
  测试 `test_unfilled_default_old_staff_off_new_hire_on` / `test_admin_matrix_default_all_after_deadline`。
- **待填报提示 + 员工落点（2026-10-01 用户补充）**：`needs_plan`（**窗口开着且未登记**才需要）、
  `staff_home`（**唯一来源**：待填报 → `/my/plan`，否则 → `/my/report`，无编号 → `/my/perf`）；
  用在 ① 登录 POST ② `GET /` ③ 中间件拦回员工时（三处同源，勿各写一遍）。
  弹窗在 `base.html`（中间件写 `request.state.plan_pending`，写明填报期到哪天；**计划页自身不弹**；
  **不用 `x-cloak`**，无 JS 也要能看见）+「出勤计划」tab 红点。首登改密优先 → `/my/password?must=1`。
  ⚠️ 改了落点会连带影响"员工访问管理端被拦到哪"的历史断言（多个测试已放宽为「员工首页之一」）。
- **入口**：员工端底部 tabbar 第 4 项「出勤计划」（`/my/plan` 已进 `STAFF_ALLOWED`）；管理端顶栏「日期计划」。
- 迁移 `c1d2e3f4a5b6`（建表）+ `c2d3e4f5a6b7`（加 `reported` 并回填历史自报）；**本地库要手工补列/补表**
  （本地不跑 alembic：`create_all` 建表 + `ALTER TABLE staff_date_plans ADD COLUMN reported ...`）。
- **写透上线前的自报要对齐一次**：`scripts/resync_plan_reported.py`（默认 dry-run，`--apply` 才写；
  线上：`docker cp` 进容器后 `docker exec deploy-web-1 python scripts/resync_plan_reported.py --from 2026-09-17 --apply`）。
  线上 2026-10-01 实测踩过：20 条自报里 11 条没有计划行 → 矩阵把"报了的人"显示成 `–`。
  回归测试 `test_resync_repairs_reports_missing_plan_rows` / `test_rebuild_reported_clears_stale_flags`。
- **下载防连点**：导出链接带 `download` 属性 + `base.html` 对 `a[download]` 做 2 秒吞点击
  （用户 2026-10-01："一次下载了两个文件" = 双击触发两次请求，线上日志可见同端口两次 GET）。
- 测试 `tests_web/test_date_plan.py`（71 项：半月划分/闰年大小月/窗口边界与间隙/写透/对齐修复/过去看事实/
  默认全出勤/新人入职补登/待填报与登录落点/弹窗与角标/窗口状态条/整行点选无 JS/矩阵无登记列与按日小计/越权/导出）；
  **路由用例用 `frozen` fixture 冻结 `date_plan.jst_today`**（否则随真实日期飘红）；
  `test_migrations.py` 已把新表列入 schema 校验。

## 发布流程（生产 = 新机，ssh 别名 store-prod；旧机已退服不再发布）
1. 本地测试过 → commit → `git push origin main`；
2. `TS=$(date +%Y%m%d_%H%M%S)`；`ssh store-prod "mkdir -p /opt/store-settle/releases/$TS"`；
   `rsync -a --delete -e ssh --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' --exclude 'data/' --exclude '.pytest_cache' --exclude '.DS_Store' --exclude 'scripts/2026-09_*' ./ store-prod:/opt/store-settle/releases/$TS/`；
3. `ssh store-prod "cp /opt/store-settle/current/deploy/.env /opt/store-settle/releases/$TS/deploy/.env; rm -f /opt/store-settle/current; ln -s /opt/store-settle/releases/$TS /opt/store-settle/current"`；
4. `ssh store-prod "cd /opt/store-settle/current/deploy && docker compose build web && docker compose up -d --no-deps web"`（entrypoint 自动 alembic upgrade）；
5. 公网走查 https://store.visitworld.me（健康/绩效/找平/对账/产品页）；
6. 改数据前先备份：`ssh store-prod "docker exec deploy-db-1 pg_dump -U store_settle -d store_settle > /opt/store-settle/backups/pre_xxx_$(date +%Y%m%d_%H%M%S).sql"`。
7. 旧机（8.216.43.224 / `ssh store-old`）仅保留数据供回滚（改回 DNS A 记录 + compose up 即恢复），不参与发布。

## 已知坑
- **店铺主档自动化（2026-09 修复）**：`auto_finalize_pipeline` 内已自动
  `store_master.build_pairs_for_entities`（增量候选对）→ `auto_merge_exact`（exact 程序必同
  自动合并，保留 id 最大为主档，跨城市组跳过）→ 有合并时 `recompute_affected_months`
  （受影响月 = 文件月 ∪ 被并实体 raw 月，逐月 rebuild+找平+看板，全部 best-effort 不阻断上传）。
  `flow.rebuild_month` 成功路径末尾自动同步找平表（返回 `synced`），并从档排除扩展到
  "有合并留痕(StoreMergeLog)的从档"，历史从档（无日志）不受影响。测试：tests_web/test_store_auto.py。
  历史坑：候选对原先从未生成 → 同店不同写法（空格/全角）各算一家店、静默多算点数（8 月多 4 条）。
- **找平债务跨月**：某人某月无工资行时，结转必须从**找平表**（`payroll_adjusts.remaining`）读，
  不能只从"上月找平行"读——否则债务丢失（实测 11 人 / -89,750 收不回）。
- **薪资四表**：`payroll_period_rows`（应发，会重算）/ `payroll_payments`（实发台账，不可改写，
  **导出发薪表即登记**）/ `payroll_adjusts`（找平进度：原始/已找平/剩余/结清时间）/
  `payroll_settlement_links`（发放↔找平 多对多，双向可查）。
- **数据自洽检查**：MCP `visit_verify` 或 `scripts/verify_payroll_logic.py`（同源，8 项互证；
  A6 是"计划变更提示"非错误）。基准数字必须与线上逐人一致（`scripts/compare_with_prod.py`）。
- MySQL TEXT 默认值需 `sa.text("('')")`；唯一键含 TEXT 列用 VARCHAR(255)。
- store_entities 自引用外键中间态需按 dialect 禁用触发器/FK 检查。
- 演示库现有 7/8/9 月（7 月为部分数据）；补传同月文件后用「月度重算」收敛口径。
- 线上演示期间别做写操作（删文件/重算/重传对账/重置口令）。
- 权限：员工访问管理页被中间件+路由双层拦截；申诉有归属校验；管理路由已补 role!=admin（纵深防御）。
