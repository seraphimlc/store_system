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
    **顶部是「自报汇总」模块**（`data-testid="report-summary"`；2026-10-02 用户连续三次迭代：
    "别只显示一行"→"做成正经模块"→"**做成 8 个小块分两行**，表格看着不好看"）：
    **两行 × 四块**（`.sum-row` + `.sum-tile`；**不写行组名**——块标签本身已说明是店铺数还是分数，
    2026-10-02 用户："这个组名去掉，多余"）——
    第一行「店铺数」= 1点店铺数 / 2点店铺数 / 总店铺数 / **2点店铺占比**（=2点店÷总店）；
    第二行「分数（点数）」= 1点分数 / 2点分数 / 总分数 / **2点分数占比**（=2点分数÷总分数）；
    两个占比块用 `.hl` 高亮 + tooltip 说明口径；卡片右侧小字是 `区间 · 第 x/y 页 · 自报条数`；
    数值由 `reports_summary()` 一次聚合查询给出、**不受分页影响**；
    对比页 `/staff-reports/compare` 逐人新增「系统分数」「自报分数」（比例在同格 hint 里），
    逐日在「系统/自报」格子的 hint 里带分数与比例；
  - **导出**：自报明细加「分数(点数)」「2点分数占比%」；对比导出（按人/逐日）加「系统分数/自报分数/占比」。
  - 实现位置：`report_compare.points_of()/p2_score_share()`（唯一算法来源）、`_row_dict()`、`compare()`、
    `reports_summary()`、
    `daily_report.month_days()`、`report_export.py`。测试 `test_points_and_rate_helpers` 等 4 项。
- **员工下拉 = 可搜索 combobox（2026-10-02 用户要求）**：`app/static/emp_select.js`
  （原生 JS、零依赖，`base.html` 全局引入）——给 `<select data-emp-filter>` 包成 combobox：
  **点开面板里自带搜索框，输入即过滤**（用户第一版反馈："为什么不是下拉框弹出的时候可以输入内容？
  你这样做还不如不做"→ **不要**在 select 上面另加一个输入框）。
  - 原 `<select>` 留在 DOM 里但 `display:none`（`.emp-cb-native`）——**表单提交值 / htmx / onchange 全部不变**；
    可见部分 = 按钮（显示当前项）+ 面板（搜索框 + 选项列表）；
  - 匹配：**编号后 5 位**（选项文本含完整编号，子串即命中）、**姓名任一个字**，查询串 **NFKC 归一**；
  - 键盘：↑/↓ 移动、Enter 选中、Esc 关闭、点面板外关闭；选中后**派发 `change`**（看板那种"change 即加载"直接出结果）；
  - 无匹配显示「无匹配」；htmx 局部替换后自动重挂。
  已挂 4 处：`/staff-reports`（筛选 + 补录卡片）、`/staff-reports/compare` 筛选、`/dashboard` 员工维度。
  新增员工下拉时**记得加 `data-emp-filter`**；测试 `test_employee_selects_have_filter`（含 combobox 结构断言）。
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
  AI 全程 mock 不连外网。i18n 巡检：`scripts/i18n_audit.py`（缺日文/死键）、`scripts/i18n_prune.py`（清死键）、`scripts/i18n_dedup.py`（查重复键/译法冲突，默认只报告）。

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

## BD 作业域（片区/派活/待扫清单，2026-10-02 起 · 分支 feat/bd-ops-layer）
> 设计全文 `docs/specs-bd-ops-layer.md`（含决策记录与实测数据）。**只做到 P0，未上线。**
- **硬边界（最重要）**：**结算域与作业域各干各的**（用户 2026-10-03 确认：任务完成情况**不影响绩效、不影响工资**）。
  作业域**只新增 `bd_*` 表**，**绝不向结算域四表加列**
  （`formal_records`/`person_daily_stats`/`month_perf_records`/`payroll_period_rows`）；**结算域永不读作业域**。
  守门测试三档：列级 `test_station_tasks_does_not_touch_settlement_tables`、
  源码级 `test_settlement_code_never_reads_ops_domain`、行为级 `test_progress_submission_only_writes_bd_tables`。
- **模型（P0）**：`BdArea`（行政区划基底 pref/city/ward/town，官方编码只读同步）/
  `BdStore`（门店宇宙，`store_key` 取既有 `raw_records.store_id_raw`）。
- **P0 已做**：① 装官方行政区划 `bd_area`（総務省 全国地方公共団体コード → 一都三県 4/212/44）；
  ② 从 `raw_records.original_row` 提取地址/业态 → `bd_store`（30,681 家，11,011 家有地址）。
  入口一条命令：`DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/bd_init.py`。
- **P0 的两条关键经验**：
  1. **`store_id_raw` 是稳定的门店主键**（一个 id → 一个店名，基本 1:1；格式 `010104709`+注册日+序号）
     → **做门店身份用它，不必依赖 Google place_id**。
  2. ⚠️ **源文件列布局逐文件不同（50/39/34/7 列）→ 禁止按固定下标解析**，
     必须按内容识别（`bd_store.find_address` 就是这么写的）。
  3. ⚠️ 総務省 Excel **第 2 表里政令市自己也占一行**，解析时必须跳过；
     区的父级靠"市级名前缀"反推（否则 `さいたま市` 会被当成区）。
- **术语**：原设计叫「作业网格（grid）」，**2026-10-02 用户否决**，改为「**片区 zone**」：
  片区=天然商圈/行政块、**大小不限**、**可由多个小队共管**（防撞下沉到**门店级待扫清单**）；
  **只有两条硬规则**（成片 + 规模 5k–8k 家），不做负载均衡强制、不做六边形、不做路径优化。
- **产能实测（2026-10-02，查 raw_data）**：正常外勤日 P50 = **128 家/人/天**（有效记录 60 家）；
  疑似批量补录仅 **4%** → 原"数据注水"的判断**推翻**。规划取值 **80 家/人/天**。
- **P1 待做（阻塞）**：门店坐标化需要 Google Places —— **API key 已配到 `.env`（`VISIT_GOOGLE_MAPS_KEY`），
  但项目未开通结算 → Places 每天仅 100 次、Geocoding 直接不可用**。⚠️ 本机访问 Google
  **必须用 Python（读 macOS 系统代理），curl 不通**（curl 不读系统代理，要显式 `-x`）。
- **已知未修**：缺陷清单见 `docs/问题单-已核实缺陷.md`（含 `period.py:93` 结转符号错等 6 项，均未修）。

## 团队 + 车站任务（2026-10-03 交付 · 分支 feat/team-task）
> 规格 `docs/specs-team-management.md`（第一期·团队定义）与 `docs/specs-station-tasks.md`（第二期·车站任务），
> 两份文档开头都有 **§0 实施记录**（权威口径）。**只新增 `bd_*` 表，不碰结算域四表、不读 `raw_records`**。

- **需求（用户原话）**：管理员建队圈人 + 指定队长 → 队长权限 → **队长端在员工端里**（任务分派 + 每日进展，
  每任务一个 **0–100% 滑动条**，三个 tab **未分配/进行中/已完成**）→ 管理端看**所有已分配任务**的状态，
  任务有**分配日期**、可按**日期区间**查 → **每个站点 = 一个任务**（后续可扩到所有地铁线路车站）。
- **角色三档**：`admin` / **`leader`（新）** / `staff`。落点**唯一来源** `app/services/home.py::landing_home()`：
  admin→`/dashboard`、leader→**`/my/tasks`**、staff→`date_plan.staff_home()`。
  ⚠️ **加第三角色会死循环**（`/ → /dashboard → _denied() → /login → /`）：修法是
  `auth_r` 的 `GET /`、`GET /login`、登录 POST 三处都走 `landing_home`（**`GET /login` 是关键那一环**）；
  各 router 的 `_denied()` 保持 `/login`（会按角色二次落点，不成环）。
- **⚠️ 队长角色必须自动同步（否则必踩）**：`bd_teams.sync_account_roles()` —— 在团队里当上队长 →
  账号 `users.role` 自动升 `leader`；不再当任何队队长 → 自动退回 `staff`（**只动 staff/leader，绝不动 admin**）。
  `set_members` 保存成员后自动调用；种子脚本也调。**本地库端到端实测踩到过**：只在团队表里指定队长、
  不改账号角色 → 队长登录进的是**队员视图**（`/my/tasks` 0 行、没有三 tab）。本地库已同步：
  5 位队长账号 = `ogawa`/`tangjing`/`ganzijie`/`luozijie`/`chenweifeng`（陈嘉溢队无队长）。
- **表（6 张，迁移 `b2c3d4e5f6a7`，down_revision `d3e4f5a6b7c8`）**：
  `bd_team` / `bd_team_member`（成员含历史，移出写 `end_date` **不删行**）/
  `bd_station`（车站主数据，`name_norm` 判重）/ **`bd_task`（1 站 1 任务：`team_id` + `assign_date` + `state` + `pct`）** /
  `bd_task_assign`（担当 **≤2 人**，必须是该队现役成员）/ `bd_task_progress`（**每日一条** `UNIQUE(task_id, date)`，当天可改=覆盖）。
- **状态机（唯一口径 `bd_tasks.recompute_state`）**：没担当→`unassigned`；有担当且 `pct<100`→`doing`；`pct=100`→`done`。
  pct 夹在 0–100；**当前进度冗余在 `bd_task.pct`**（列表直读）。
- **权限**：`can_report`（管理员/该任务队长/**本人是担当**）、`can_assign`、`can_adjust`（管理员/该任务队长，走 `bd_teams.is_leader_of`）；
  队员**能上报自己担当的**（有滑动条），但不能分派/不能改别人的。**换队 `set_task_team` 会清空原担当**。
- **页面**：管理端 `/teams`（+`/teams/{id}` 圈人）、`/stations`（批量建任务/批量派队）、`/tasks`（总表+日期区间+导出）；
  员工端 `/my/tasks`（**同一页**：队员=我的任务+上报；队长=多出 我的/未分配/进行中/已完成 四 tab + 分派 + 调整）。导航：`base.html` 管理菜单改 `role == 'admin'`，
  底部 tabbar 对 `staff`+`leader` 都显示并新增「任务」tab。
- **种子导入（真实数据已入库）**：`scripts/bd_seed_team_task.py --src "/Users/liuchang/Desktop/万总/team_task"`
  （**默认 dry-run**，`--apply` 才写）。实测：**6 队 + 515 站 + 515 任务**，`assign_date`=运行日；
  队长名模糊匹配（`小川`→**`小川逸`**）；⚠️ **陈嘉溢有两条同名人员记录 → 只报警不挂队长**（按"命中多条不猜"）。
- **关键事实（实测，别再猜）**：`万总/team_task/*.xlsx` 是**已定义好的任务清单**（列：车站/担当/开始日/完成日/状态，
  除车站外全空）；515 站**零重复、队间不重叠**；与上游 `task_plan/407站-小区域执行计划.xlsx` **只重合 189 个**
  （10-03 是重新划分，**以 515 为准**）。`车站` = 用户说的"一片区域"（站前商圈），**系统不记录店明细**。
- **踩过的坑**：① 导出文件名含中文 → 响应头 latin-1 报错，必须 `filename=` 用 ASCII、中文走 `filename*`；
  ② `create_all` 前必须先 `import app.models`（否则建不出表）；③ 本地库不跑 alembic → 手工
  `create_all` 补 6 张新表（只建缺失表）；④ **heredoc `python3 <<EOF` 会静默失败**（用 sed 或写脚本文件）；
  ⑤ `tests_web/test_date_plan.py` 有 4 个用例没冻结 `jst_today`（跨过 10-03 截止日必红，基线同样失败）→ 已补 `frozen`。
- **第二轮增量（2026-10-03 讨论「队长也要巡店」后）**：
  ① **队长页第 4 个 tab「我的」**（`/my/tasks?tab=mine`）= 他当担当的**全部**任务（**跨队也算**；
  旧实现只看他带的队 → 他在别队被派的活看不见，是真缺口）；顶部显示「我负责 N 条」。
  ② ~~**提交进展仍只有队长**~~ ← ⚠️ **已被第三轮取代**（见下）：现在是「员工先上报、队长调整」；
  行内按 `can_submit` 决定给不给滑动条。**这不是 bug，是口径**。
  ③ **开始日/完成日**（`bd_task.start_date`/`done_date`，迁移 `c1b2a3d4e5f6`）：首次提交=开始、
  pct=100=完成、**回退清完成日**；管理端/导出/队长页都显示。
  ④ **管理端"谁没动"抓手**：`days_since` + `stale`（未完成且 ≥2 天没动）→ 行高亮 `.stale-row`、
  `?stale=1` 只看停滞、**按队汇总表**（总数/未分配/进行中/已完成/停滞）。
  ⑤ **状态机修正（真 bug）**：旧口径"没担当→未分配"会让**有进度但没分人**的站显示成未分配
  （队长自己开工就踩）→ 改为 **已完成 > 进行中（有担当 或 有进度）> 未分配**。
  ⑥ 移出成员时若他还有未完成任务 → **提示不自动改派**。
  ⑦ 用户决定**往后放**：线路补全（PDF 里有 `XC01 井の頭線 11站/P2` 可抽）+ 批量导入界面。
  ⑧ ~~未定：队员能否填自己的进展~~ ← **已在第三轮定案**：员工能上报自己的（见下）；队员看备注仍未定。
  测试 `tests_web/test_team_task.py` **37 项**；全量 `tests_web` **364 passed**。
- **第三轮增量（2026-10-03 用户总结 4 张表 + 口径纠正）** —— ⚠️ **本轮推翻第二轮的 ②**：
  ① **进展改为「员工先上报、队长调整」**（用户："员工自己先上报，队长做调整"）。判权拆两个：
  `can_report`（管理员 / 该任务的队长 / **本人是担当**）、`can_assign`+`can_adjust`（管理员 / 该任务的队长）。
  **队员端任务页从只读变可写**（有滑动条）；不能分派、不能碰别人的任务。旧测试
  `test_member_is_readonly` 已改名为 `test_member_can_report_own_but_not_others`。
  ② **队长 = 员工 + 任务管理**（用户原话）：同一页 `/my/tasks`，队长多出 我的/未分配/进行中/已完成 四个 tab
  （**默认「我的」**，与员工一致）+ 分派与调整控件。
  ③ **日志 `bd_log`（一张通用追加表）**：domain(task/team/member/station) + ref_id + **ref_label 冗余可读**
  + action + field + old_value→new_value + actor/actor_name + created_at；**只写不改不删**。
  写入点：任务创建/派队(含分配日期)/分派担当(谁进谁出)/进展上报与状态变化/车站改名与线路；团队建/改/停用；
  成员加入/移出(**记录保留**)/队长任免。查看：`/tasks/{id}` 详情（进展历史 + 时间线，可见=管理员/该队队长/担当）
  + 管理端 `/logs`（按 domain 过滤；队长只看本队相关）。
  ⚠️ 价值点：**每日进展表会被覆盖，日志不会** —— "员工报 40% → 队长改 60%"查得到。
  ④ **权限表（用户选 a 方案）`bd_role_cap`**：`(role, capability) → allowed`，9 个能力；
  **判权与页面按钮共用一份**（`app/services/bd_perm.py`；表为空回退 `DEFAULT_CAPS`，**读路径不写库**）。
  ⑤ **队员状态直接用员工状态**（用户口径）：`bd_team_member` **不另造状态**；`team_members()/person_options()`
  带出 `users.status`（在岗/请假/停用/离职/未开通账号），团队详情页新增「员工状态」列。
  ⑥ **离职/停用不能派工**（`assign_members` 拒绝 + 派工候选不列出）；**成员记录保留**，任务**可改派**。
  ⑦ **任务名先用车站名**（不加独立字段，用户："先这样定义，后面可以改"）。
  ⑧ **线路补全做了**（用户："有就加"）：5 份执行说明 PDF 页头有线路（`XC01 · 井の頭線`）→
  `scripts/bd_extract_lines.py` 抽 **53 线 / 576 站** → `scripts/bd_station_lines.json`（**随发布走**；运行时不需要 pypdf）→
  `scripts/bd_backfill_lines.py` 回填 → 本地库 **422/515 站有线路（82%）**；⚠️ 陳偉鋒队**无 PDF** → 82 站留空。
  ⑨ **仍未定**：队员能否看任务备注（现在仍只有队长/管理员可见）；车站批量导入界面（往后放）。
  迁移 `d1e2f3a4b5c6`（2 张表 + 27 行能力种子）；本地库 `create_all` + `bd_perm.seed()`。
  测试 `tests_web/test_team_task.py` **46 项**；全量 `tests_web` **373 passed**；i18n 0 缺失 0 死键。（第四轮后：**56 项 / 383 passed**）
- **第四轮增量（2026-10-03 假期模式 + 派工提醒）**：
  ① **`bd_staff_leave`（休假期）**：`end_date` 空 = 未定；**一人同时只有一条 `active`**（新开→旧的自动
  ended 并把 end_date 收到新开始日前一天）。员工端入口 = **出勤计划页顶部「假期模式」卡片**
  （开启/结束；结束 = 从当天起不算休假）——**不做第 5 个 tab**（H5 底栏只有 4 项，且它与出勤计划同源）。
  ② **可用性检查唯一入口** `bd_leave.availability_map(db, codes, date)`（**页面与写端点共用**）：
  离职/停用 → **block**；休假期覆盖该日 / `users.status=请假` / `staff_date_plans` 该日不出勤 → **warn**。
  ③ **只提醒不阻断**（用户原话："要有提醒，但不强制约束"）：`assign_members` 返回 `warnings`，
  队长端分派后消息追加「⚠️ 汤静：休假中（至 X）」，**分派照旧成功**；只有离职/停用仍 raise。
  ④ **提醒看哪一天 = `max(分配日期, 今天)`**（任务没开始看开始那天；已开始/过期看今天）——
  否则拿过期分配日期判断，提醒永远落不到点上。
  ⑤ 候选旁边打可翻译的 `休假/计划休/请假` 标签；管理端 `/tasks` 的担当后面标「休」；
  员工端 `/my/tasks` 有「你现在处于休假状态」横幅。
  ⑥ ⚠️ **顺带修真问题**：`/my/plan` 原 `role != "staff"` → **队长进不去出勤计划页**（与"队长也是员工"矛盾）
  → 改为 staff/leader 都能用，并把 `/my/leave` 加进中间件白名单。
  ⑦ **跨域只读**：作业域**只读** `staff_date_plans`/`users`（派工提醒），**绝不写**；写只写 `bd_staff_leave`。
  守门测试 `test_availability_reads_never_write`（可用性检查**一个表都没碰**）。
  迁移 `a3b4c5d6e7f8`（只加一张表）；开/结束都写 `bd_log`。
  测试 `tests_web/test_team_task.py` **56 项**；全量 `tests_web` **383 passed**。（第五轮后：**66 项 / 393 passed**）
- **第五轮增量（2026-10-03 进展确认/调整 + 站内消息）**：
  ① **进展审核**：`bd_task_progress` 加 `reported_pct`（**员工原值，永不覆盖**）/`reported_by`/
  `review_status(pending/confirmed/adjusted)`/`reviewed_by`/`reviewed_at`/`review_note`。
  `pct` = 当前生效值 → 能展示"队员报 80%（已调成 70%）"。
  ② **队长两个动作**：**确认** `POST /my/tasks/confirm`（认可原值，一键）/ **调整** 走
  `POST /my/tasks/progress`（值与上报不同即 `adjusted`）。队长页新增**「待确认」tab**（否则藏在"进行中"里找不到）。
  ③ **确认/调整都自动发消息给员工**（`scope=task_review` + 直达 `/tasks/{id}`，文案含原值→新值与操作人姓名）；
  发件人 = **做事的人**（队长/管理员），不是无名 system。没有员工上报时队长直接填 → **不发消息**。
  ④ **消息模块 `docs/specs-messages.md`**：`bd_message`（1 条消息）+ `bd_message_recipient`（每人一行、按人已读）；
  一个/多个/全体都是同一条消息。`/messages` 三 tab：收件箱（只看未读/全部已读/单条已读）/ 我发出的（已读人数）/ 发消息。
  未读红点由中间件注入 `request.state.unread_messages`（一次 COUNT）→ 底栏「消息」tab + 管理端顶栏「消息（N）」。
  `GET /messages/{id}/go` 标已读并跳到目标页；不是我的消息 → 回列表且不改已读。
  ⑤ **收件人隔离唯一入口** `bd_msg.recipients_for()`：管理员 → 指定人/全体；**队长 → 只能本队现役队员**
  （"全体"= 我的队全体），**越界拒绝且不发**；员工不能发。
  ⑥ ⚠️ **管理员账号可能没有 `person_code`** → 消息模块的登录判据不能用它（否则管理员连发件箱都进不去），
  管理员特判放行（收件箱空而已）。
  ⑦ 边界不变：只写作业域表；守门测试 `test_message_send_only_writes_bd_tables`。
  迁移 `b4c5d6e7f8a9`（2 张新表 + `bd_task_progress` 加 6 列）。
  测试 `tests_web/test_team_task.py` **66 项**；全量 `tests_web` **393 passed**。（第六轮后：**69 项 / 396 passed**）
- **第六轮增量（2026-10-03 假期模式「可以标」出勤计划）**：
  ① 开假时把休假期内的出勤计划**自动标成不出勤（×）**：`staff_date_plans` 加 `leave_id`（可空）
  + `source='leave'` → 管理端矩阵那几天就是 ×（**不做"谁在休假"一览页**，用户说"不要看"），
  `title` 标「（假期模式）」区分"休假"与"自己点的不出勤"。
  ② **三条"不抢"规则**：今天之前不标（过去看事实）/ 已自报的不标 / **员工自己点的 × 不认领**。
  ③ **结束或替换休假 → 按 `leave_id` 精确撤销**：`reported=False` 的行删掉（恢复默认规则，
  不伪造"已登记"），`reported=True` 只解绑保留事实。
  ④ **表的主人仍是日期计划模块**：新增 `app/services/plan_leave.py`（`apply_leave`/`clear_leave`），
  作业域**调用它**而不是自己 `StaffDatePlan.add/delete` → §0.3 边界放宽为"只读 + 经这一个接口写"。
  迁移 `c5d6e7f8a9b0`（只加一列）。测试 `tests_web/test_team_task.py` **69 项**；全量 **396 passed**。
- 测试 `tests_web/test_team_task.py`（**69 项**：团队/成员历史/队长无重定向环/导航 gate/状态机/≤2 人/每日覆盖/
  员工可上报自己的/跨队担当可上报/开始完成日/停滞筛选/移出提醒/日志写入与时间线/数据隔离/离职不可派工/角色能力表/**域边界（列级+源码级+行为级）**/假期模式开启结束与隔离/派工提醒四态/进展确认调整与消息通知/消息收件人隔离/休假自动标计划与精确撤销；
  **全量 `tests_web` 396 passed**；i18n 巡检 0 缺失 0 死键。

- **管理端导航改左侧（2026-10-03 用户："顶部的菜单换成左边的吧，顶部内容太多了，放不下了"）**：
  管理端 `base.html` 的横排 `topnav` → **左侧分组菜单 `<aside class="sidenav">`**
  （概览｜结算｜员工｜作业｜系统｜账号 六组；当前页高亮；消息带未读角标）；
  顶部只留**品牌 + 账号名 + 汉堡按钮**，账号操作（改密/中日照/退出）在侧栏底部。
  `body.has-sidenav` 让内容区 `margin-left:210px`（≥861px）；**≤860px 变左侧抽屉**
  （`.sidenav.open` + `.nav-mask` 点空白关闭），复用同一个 `menu` 开关。
  ⚠️ **员工/队长完全不受影响**：他们仍是顶部细条 + 底部 tabbar（`topnav` 只在非管理端渲染），
  所以按角色取 DOM 的测试/自动化不能假设"一定有 topnav"——`test_base_mobile_hamburger_menu`
  已改成按角色断言（管理端 `sidenav` / 员工 `topnav`）。
- **i18n 重复键巡检工具 `scripts/i18n_dedup.py`**（默认只报告）：多轮补日文时用同一锚点插入会累积重复键。
  Python dict 取**最后一个**值，功能不出错，但会**掩盖两种译法**——实测 10 个词有两套日文
  （`状态` 状態/ステータス、`在岗` 在職/在籍、`员工编号` 従業員番号/従業員コード、`共` 合計/計…），
  当前生效的是**后出现**的那个。`--apply` 只删"整行单键"的安全重复项；**同行多键的不自动改**（要人定译法）。
- **表格样式必须带 `grid-tbl`（2026-10-03 真 bug）**：`app.css` **没有通用 `table` 规则**，
  表格样式全挂在 `table.grid-tbl` 上（内边距/行分隔线/表头底色+**粘性表头**/悬停高亮/窄屏首列吸附）。
  作业域新模板（bd_*/messages）当时写了**裸 `<table>`** → 渲染成"没边框没内边距、挤成一坨的文字"，
  用户反馈"圈选队员--做成一个表格"（它本来就是 table，只是没有表格样式）。已全部补 `grid-tbl`；
  `config.html`/`file_layout.html` 两张键值表单表补 `kv`。守门测试 `test_ops_tables_use_grid_tbl`
  （作业域模板里出现无 class 的 `<table>` 直接红）。**新建表格一律 `class="grid-tbl"` + 外面包 `.tbl-wrap`。**
- **「圈选队员」重做（团队详情页）**：从"一列复选框"变成**带工具条的真表格** ——
  顶部一行：`筛选输入框` + `已勾选 N / 共 M 人 · 可见 K 人`（实时） + `全不选` + `保存成员`（按钮上移，
  别沉到 500 行下面）；列 = 进队 / 队长(radio，队里只能 1 人) / 姓名 / 员工编号(显示后 5 位，title 全号) /
  账号(已开通·未开通) / 员工状态(**色块**：在岗绿/请假黄/离职停用灰) / 原角色；
  **勾选行整行淡蓝高亮**（`.pick-bar` + `tr.picked` CSS）。提交字段不变（`person[]` / `leader`）。
  ⚠️ **队长怎么指定**（2026-10-03 用户问"如何指定队长？"）：勾「进队」+ 点该行「队长」两步。
  旧实现 `entries` **只遍历 `person`** → 只点「队长」不勾「进队」时**静默丢弃**（用户以为指定了却没生效）。
  已两端修：① 点「队长」**自动勾上「进队」**（`bdLeaderPicked`，页面上写了操作说明）
  ② 后端把 `leader` 里的人**兜底并进 entries**（JS 没跑也不丢）。回归测试
  `test_leader_set_even_if_not_ticked_as_member` + `test_team_detail_leader_howto_and_autocheck`。
- **「一个队员只能在一个队」（2026-10-03 用户口径）**：圈选候选列表 = **本队现役成员 + 自由人**；
  已在别队的人**不显示**（提示行给出被隐藏人数 `data-testid="n-elsewhere"`）。
  `person_options(db, team_id=...)` 传 `team_id` 才过滤（不传 = 老行为，别处复用不受影响）；
  唯一判据来自 `bd_teams.active_team_of(db, exclude_team_id=...)`（现役成员 → 队名）。
  **写入端也拦**（`set_members` raise「这些人已在别的队，请先在原队移出」）—— 界面隐藏只是第一层，
  构造 POST 仍可绕。
  ⚠️ **死规定（用户 2026-10-03 明确："不存在跨队借调的情况。只有转出再转入。这是死规定。"）**：
  **已钉到数据库层** —— `bd_team_member` 部分唯一索引 `uq_bd_team_member_active_person`
  （`person_code` 在 `end_date IS NULL` 上唯一；SQLite/PG 支持，MySQL 跳过、靠服务层）。
  任何代码路径 / 手工 SQL / 并发想让一人同时在两队 → **IntegrityError**；
  `set_members` 捕获它并转成一句人话（"保存冲突：有人刚被别的队圈走了…"）而不是 500。
  于是 `leader_teams()` 复数（一人带多队）与跨队担当**都不可能再发生**；
  旧测试 `test_leader_sees_and_reports_own_cross_team_task` 已改名改口径为
  `test_leader_cannot_be_in_two_teams_and_cannot_see_other_team_tasks`，
  并 +`test_db_level_single_team_constraint`（绕过服务层直插 → 数据库拒绝；转出→转入仍可行）。
  迁移 `d6e7f8a9b0c1`。调人流程：**原队取消「进队」保存 → 新队再圈进来**。
  自检工具 `scripts/check_single_team.py`（只读）：一人多队 / 悬空担当 / 无队长的队 / 索引是否存在，
  有违规则非 0 退出（可当发布验收）。
  ⓘ 2026-10-04 核查：队5（陈嘉溢队）的 7 名成员 + 队长 アサダ 是**用户自己在界面上设的**（不是脏数据）。
- **模板 HTML 结构自检 `scripts/check_templates.py`**：Jinja 不校验 HTML，切文件（head/tail 拼接）
  容易把标签切坏（当天就切掉了 `<form>` 的续行）。用法 `./.venv/bin/python scripts/check_templates.py`，
  输出"检查 N 个模板，M 个标签不配对"（0 为正常）。**改完模板顺手跑一次。**

- **「圈选队员」改回普通表格（2026-10-03 用户："圈选队员的表格太丑了。做一个普通的表格不行吗？"）**：
  上一版我自创了三样东西，都是"丑"的来源，**全部去掉**：
  ① `.tbl-wrap` 上的 `max-height:26rem;overflow:auto`（表格被塞进小滚动窗口 + 卡内吸顶表头）；
  ② 自创工具条 `.pick-bar`（62% 宽输入框 + 按钮混排）；
  ③ 勾选行整行染色 `tr.picked`，以及员工状态列的**彩色药丸**（改回纯文本，跟 /团队 列表一致）。
  现在是**标准结构**：`筛选(label.f)` → `.form-actions`（保存成员 / 全不选 / 已勾选计数）→
  `.tbl-wrap > table.grid-tbl`（全页滚动，不用嵌套滚动区）→ 两条说明；只有
  `.pick-cell { width:3.6rem; text-align:center }` 一个自定义类（进队/队长两列窄且居中）。
  ⚠️ **教训：新页面优先复用既有组件与既有页面版式**（`/stations`、`/tasks` 长什么样就照做），
  不要为单个页面自创滚动容器/工具条/彩色标记。守门测试 `test_team_detail_pick_table_plain`
  反向断言 `.pick-bar` / `max-height:26rem` / `picked` / 彩色药丸**都不存在**。
- **i18n 工具两个真 bug（2026-10-04 修）**：
  ① `scripts/i18n_prune.py` 的正则只认**单行**词条 → **多行**（值在下一行）的死键
     "每次都报告已删除、实际没删"（假成功，死键一直清不掉）。已抽出 `remove_keys()` 处理单/多行，
     没匹配上会**明确报出来**；回归测试 `test_i18n_prune_removes_single_and_multiline_keys`。
  ② `scripts/i18n_dedup.py` 的"译法冲突"比的是**整块文本** → 多键行（一行挤着多个 key）
     会**误报冲突**。已改为按**键值对**比较（`PAIR_RE`）。修正后真实冲突只有 **1 个**
     （`员工编号` 従業員番号/従業員コード，已统一为当前生效值，**译文可见行为不变**）；
     之前报的"10 个词两套译法"是误报。清理 33 行重复键后**生效译文 0 变化**（键数 907→906 只少 1 个死键）。
  + 新增 CI 兜底 `test_i18n_dict_is_clean`（跑 audit，缺日文/死键非 0 直接红）。

- **表单/筛选行统一（2026-10-03 用户："团队页面…『查』这算什么事？放不下两个字吗？" +
  "新建团队的UI也特别丑，你放到一行里，对齐不行吗？"）**：
  ① 全站按钮文案 `查` → **`查询`**（9 个模板：teams/stations/tasks/my_tasks/stores/store_entities/
     file_report/staff_reports/staff_report_compare）。**别为省地方把按钮裁成一个字**。
  ② 筛选行与新建表单**一律照抄项目既有标准写法**：
     `<div class="card"><form class="form-grid">` + `<label class="fld">标签<input></label>` ×N +
     末尾 `<div class="form-actions" style="align-items:flex-end">按钮</div>`
     （范例：`staff_report_compare.html` / `staff_reports.html`）。
     `.form-grid` = `auto-fit minmax(170px,1fr)` → **宽屏一行、窄屏自动一列**（≤760px）；
     `align-items:flex-end` 让按钮与输入框**底部对齐**。
     ⚠️ 反例（改之前的样子）：用 `label.f`（inline-flex，标签与输入同排）+ 某些输入框写 `width:100%`
     → 各列宽度不一、标签跳动；按钮另起一行；`<details>` 里再塞一个不成形的表单。
  ③ 团队页/车站页的「新建」从 `<details>` 折叠改为**常显卡片 + h3 标题 + 一行对齐表单**
     （用户口径："放到一行里，对齐"）；每张筛选卡加「清空」（有筛选条件时才出现）。

- **全站列表分页（2026-10-03 用户："你就不能做个分页吗？你查查有多少地方可以做成分页的"）**：
  **审计结果（实测行数 / 修前状态 / 现状）**——
  | 页面 | 行数 | 修前 | 现在 |
  |---|---|---|---|
  | /stations 车站 | 515 | 全量铺一屏 | ✅ 50/页（11 页），筛选条件带进翻页链接 |
  | /staff-admin 员工管理 | 54 | 全量 | ✅ 50/页 |
  | /stores/entities 店铺实体 | **42,140** | `limit(500)` **硬砍** + 42k 全查进内存 | ✅ 50/页（843 页），`by_id` 只装本页 |
  | /messages 收发箱 | — | service `limit=100` 截断 | ✅ 50/页 |
  | /tasks、/logs、/staff-reports、/files | 515/486/17/5 | 已有分页 | 保持 |
  | /perf 83、/staff-plans 99 | — | 按月/按人范围 | 不需要（说明在回复里给了） |
  | /teams/{id} 圈选队员 | ≤55 | 全量 | **故意不分页**：那是 checkbox 表单，翻页会**丢未保存的勾选**；有筛选框即可，真要分页得做"选择跨页携带" |
  - **唯一实现**：`app/services/paging.py`（`paginate(query, page, per)` 出一份完整 pager dict +
    `qs(params)` 生成保留筛选条件的查询串）+ `app/templates/_pager.html`（首页/上一页/下一页/末页 +
    「共 N 条 · 第 x / y 页（a–b）」；**单页时只显示「共 N 条」**，不显示翻页按钮）。
    `.pager` 样式在 app.css。**新页面要有分页就直接用这两个，别再自己写一套。**
  - ⚠️ **批量操作必须作用于全集，不能只看当前页**：`/stations` 的「全部建任务」改用
    `bd_tasks.station_ids_without_task()`（原来遍历 `list_stations()` 的返回值，加分页后会**只建当前页**）。
    同理 `list_stations(only_without_task=)` 的过滤**挪进 SQL**（`NOT EXISTS`），否则 total 会算错。
- **两个真 bug（2026-10-04 顺手挖出）**：
  ① `store_entities.html` **模板编译失败**（i18n 批量包裹工具留下 `or '({{ t('无名)') }}'` 引号套引号）
     → `/stores/entities` **一直是 500**，但没有任何测试渲染过它。已修，并加
     **`test_every_template_compiles`**（把所有模板编译一遍，成本极低，专门防这类"页面直接崩"）。
  ② `/stores/entities` 的 `limit(500)`：42,140 家里后 41,640 家**永远看不到**（静默丢数据）。

- **一都三県「线路 + 车站」全量入池 + 车站即任务（2026-10-05 用户："把一都三县所有的地铁线和车站都收集进来"
  / "每个车站都是一个任务，自动创建就行" / "车站即任务" / "任务分为已完成，已分配，未分配几个tab页"
  / "可以根据线路做查询" / "任务编号不用显示" / "后台存储肯定是要分开的，未来我们会以片区当成任务"）**：
  - **数据源 = 国土数値情報 N02（鉄道）**（MLIT 官方，免费；`https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-22/N02-22_GML.zip`）。
    实测：全国 10,220 条车站记录 → **一都三県 1,949 条 → 去重 1,920 个车站 / 131 条线路**
    （東京 871 / 神奈川 408 / 千葉 383 / 埼玉 258）。**N02 的车站属性里没有都道府県** →
    必须用坐标 + 都道府県边界做点在多边形内判定（边界取自 dataofjapan/land `japan.geojson`，13MB）。
    字段：`N02_003` 路线名 / `N02_004` 运营公司 / `N02_005` 站名 / `N02_005c` 駅コード /
    **`N02_005g` 同一駅グループコード（将来合并同名跨线车站的钥匙）**；`N02_001×N02_002` 是铁道区分
    （实测归纳成 kind：jr/shinkansen/private/public/third/monorail/agt/tram/cable）。
    ⚠️ **N02 会把同一个车站按站台/区间重复记录**（山手線「新宿」记了 4 条，坐标差 100~300m）→
    按 (线路, 站名) + 坐标邻近(<1km) 去重（合并 29 条）。
    抓取脚本 `scripts/bd_fetch_rail.py`（可换版本重跑）→ 数据落 `scripts/bd_kanto_rail.json`（434KB，
    **随仓库走，导入时不需要网络**）。
  - **一线一站（用户口径："同名车站…先分哪个线的就按哪个线的来，后面再定规则"）**：
    `bd_station` 唯一键 从 `name_norm` 改成 **`(line_id, name_norm)`**（部分唯一索引，SQLite/PG；
    `line_id IS NULL` 的行用另一个部分唯一索引保证站名不重复）。所以 渋谷/池袋/東京 等会**按线路各存一行**
    （实测同名最跨 7 条线）。`group_code` 已存好，合并时用它 + 坐标即可（**本轮不做**）。
  - **新增 `bd_line`（线路主档）** + `bd_station` 加 `line_id/operator/pref/lon/lat/ekicode/group_code/source`；
    **`bd_task.source_type`**（今天恒为 `station`；将来片区 = 加 `zone_id` 可空 + `station_id` 改可空 +
    `source_type='zone'`，任务表别的都不动）。
    ⚠️ **线路名一律用 `bd_lines.line_label()` 显示**（`operator_short + name`）：MLIT 官方线路名会重名
    （「本線」有京急/京成/西武/東武…；都営叫「12号線大江戸線」）→ 简称表在 `OPERATOR_SHORT`。
  - **导入 = 幂等 + 必须认领历史站**（`app/services/bd_import_rail.py`，CLI `scripts/bd_import_rail.py`，
    默认 dry-run、`--apply` 才写）：现有 515 个车站（来自 `万总/team_task/*.xlsx`，**带着队伍与担当**）
    必须被**认领**（enrich 而不是新插）→ 否则同一个车站会有两个任务、队伍的派工白做。
    认领顺序：① (line_id, name_norm) 已存在 → 用它；② 同名且 `line_id IS NULL` 的历史行：唯一候选就认领，
    多候选时用**各自**的 `line` 串归一后比对线路名，唯一命中才认领（否则记账进 `unmatched`）；③ 其余新建。
    **实测：1,920 = 认领 515 + 新建 1,405，515 个派工与任务全保留，认领后 0 悬挂**。
    ⚠️ **老库的 `name_norm` 是旧口径写的**（`ケ` 没统一成 `ヶ`：库里"箱根ケ崎"、新口径"箱根ヶ崎"）→
    导入时**顺手改写旧键**（`rep["renorm"]`），否则同名却认领不到而重复建站（实测 3 个）。
  - **`norm_name()` 加了 ケ/ヶ 统一**（`app/services/bd_tasks.py`，全站唯一入口）：
    `箱根ケ崎/箱根ヶ崎`、`茅ケ崎/茅ヶ崎`、`南阿佐ケ谷/南阿佐ヶ谷` 必须同键 —— 实测有 3 个站因这点对不上。
  - **任务页 `/tasks` 三个 tab**（用户："任务分为已完成，已分配，未分配"）：tab 按**有没有人管**分，
    **不是按 `state`** —— 因为"派给了团队但还没分到人"在 state 上仍是 `unassigned`，而管理员眼里
    那是**已经派下去了**（515 个历史任务全属这类）：
    `done` = pct=100；`assigned` = 未完成 且（有队伍 或 有担当 或 有进度）；`unassigned` = 未完成 且 三无。
    `bd_tasks.BOARD_TABS` + `_apply_tab()` + `tab_counts()`（**计数只受其它筛选影响，不受当前 tab 影响**）。
    **默认 tab = 未分配**（1,405 个待派活是管理员的行动面）。`tab_counts`/`task_board` 都走 SQL 子查询。
  - **按线路查询**：任务页与车站页都有线路下拉（131 条，显示 `运营商简称 线路名（站数）`）；
    `task_board(line_id=)`/`list_stations(line_id=)`。⚠️ **`kw` 关键词必须在 SQL 里过滤**（1,920 个任务 +
    分页，只在当前页过滤等于"搜不到"）；⚠️ 关键词子查询里 **`BdLine` 必须显式 join**，只写
    `BdLine.name.like()` 会跟 `bd_team` 笛卡尔积 → "任何一条线路命中 = 所有任务命中"
    （实测搜「井の頭」返回全部 1,405 条）。
  - **任务编号不显示**（核查过：任务页/详情页/车站页都没有编号列，只有表单隐藏字段 `task_id`）。
  - 迁移 **`f9a8b7c6d5e4`**（新表 bd_line + bd_station 换键加列 + bd_task.source_type）。
    本地库不跑 alembic → `scripts/bd_migrate_rail_local.py`（**不 import alembic**：本机 iCloud 会把 venv
    文件驱逐成 dataless，import 直接超时）。本地 SQLite **不能 drop 唯一约束** → 用
    「建新表→拷数据→删旧表→改名」（**不要**先 RENAME 旧表：SQLite ≥3.25 会改写别表的 FK）。
    ⚠️ `to_metadata()` 必须传**同一个 metadata**（新表的 FK 指向 bd_line，空 MetaData 解析不到目标表）。
  - **踩过的坑（都写死了测试）**：
    ① **alembic revision id 撞车 = 环**：新迁移用了已被占用的 id（它是另一条链的父节点）→
       `CycleDetected`，`alembic heads` 直接报错。**新建迁移先查 id 是否被占用**。
    ② **真实导入不能用 dry-run 的临时负 id** → 131 条线路全成了负 id（URL 里 `?line=-19`）。
       已修（dry 才发临时 id）+ `scripts/bd_fix_line_ids.py` 修数据（先把旧行改成唯一占位再插正 id 行，
       否则撞 `uq_bd_line_op_name`）+ 测试 `test_import_rail_ids_are_positive`。
    ③ `STATE_LABELS` 漏了 tab 键 `assigned` → 渲染出「已分配」**空标签**（页面显示「（515）」）→
       测试 `test_board_tab_labels_all_present` 兜住。
    ④ **本机 iCloud「优化存储」会把 `~/Documents` 里的文件驱逐**（`ls -lO` 显示 `dataless`）→
       读文件超时（venv 里 116 个 .py 中招，`import alembic` ETIMEDOUT）。**开 VPN 时 iCloud 拉不回来。**
       遇到就绕开（不 import 那个包）或让用户把项目移出 `~/Documents` / 关掉 iCloud 同步。
    ⑤ 任务页分页改用统一组件 `app/services/paging.py` + `_pager.html`（`per=50`）。
  - 测试：`tests_web/test_team_task.py` **87 项**（+数据文件自洽/导入幂等/认领历史站并保任务/旧键改写/
    三个 tab 与计数/线路过滤与 SQL 关键词/正 id/标签齐全）；全量 `tests_web` **420 passed**
    （`test_oauth.py::test_e2e_oauth_token_calls_mcp_tool` 在**未改代码的干净树上也失败**（MCP 子进程 502）
    → 环境问题，与本轮无关）。

- **车站数据资产化：物理车站层（2026-10-05 用户："你把车站的数据好好整理一下，以后有更大的用处。
  车站数据可以当成我们的数据资产。也是任务的输入源之一。"）**：
  - **三层资产结构**（这次补齐了"上家"）：
    `bd_line`（线路 131）──1:N──> `bd_station`（**站×线** 1,920）──N:1──> **`bd_station_place`（物理车站 1,568）**
    任务 `bd_task` 挂**站×线**那一层（**本层不改任务行为**）；将来要"一个物理车站一个任务"时把任务指到 place。
  - **分层键 = N02_005g 駅グループコード**（MLIT 官方"同一车站"分组，**不用猜**）。
    实测：1,920 条站×线 → **1,568 个物理车站**；**237 个跨线站**（渋谷/新宿/横浜/大宮/池袋 最多 **7 条线**）；
    **同组站名 100% 一致、坐标 100% 在 1km 内（0 异常）**。place 存 name/name_norm/pref/city/lon/lat/
    group_code/**n_line/n_operator/operators/lines_text（冗余可读）**/source。
  - `bd_places.rebuild_places(db, dry)`：**幂等重建**（按 group_code 归组 + 回填 `bd_station.place_id`），
    导入流程（`import_rail` 第 ⑤ 步）与 `create_station` 都会调 → 资产永远跟着数据走；
    重建会**清理孤儿 place**（派生数据，可再生）。
    ⚠️ 手工站的复用键必须是 **站名+县**：拿 `place.id` 去比 `station.id` 会每次重建都新建一个 place、
    旧的变孤儿（2026-10-05 踩到，测试 `test_place_layer_groups_by_group_code` 抓到）。
    ⚠️ `n_line` 语义 = **不同线路数**（手工站可为 0）→ 体检对照必须 `COUNT(DISTINCT line_id)`（不是 `COUNT(*)`）。
  - **资产体检 `scripts/check_station_asset.py`**（只读，非 0 退出可当发布验收）：规模（三层数量/跨线站/
    按县/按类型）、完整性（缺线路/缺物理车站/缺县/缺坐标）、一致性（同组站名一致、`n_line` 对照、
    `n_station` 对照、同线不重名、无孤儿 place）、**与数据源对齐**（`scripts/bd_kanto_rail.json` vs 库里
    N02 派生行）。⚠️ 对齐口径用 **`group_code != ''`**，不能按 `source='mlit'` —— 认领的 515 行是从种子表建的
    （`source=manual`）但属性来自 N02。实测：**资产健康 ✓**。
  - **资产导出 `scripts/export_station_asset.py`**（`--out`，默认 `data/`）：两份 CSV、**utf-8-sig**
    （Excel 双击不乱码）——`stations_places.csv`（物理车站层）+ `stations_lines.csv`（站×线层，
    **含任务状态/队伍/担当**，因为"任务就是挂在这一层的"）。
  - 界面（`/stations`）：统计卡显示资产三层（线路 / 车站（站×线）/ 物理车站 / 跨线车站）+ 跨线站在站名后
    显示「N 条线」（tooltip 列出经过的线路）；**顺手删掉了那列 `#任务id`**（用户要求"任务编号不用显示"，
    之前用"编号"两个字搜没搜到，是 `#{{ r.task.id }}`）。
  - **未做的部分（明确留给下一轮）**：① **市区町村**（`bd_station_place.city` 已建列、现在是空的）——
    需要行政区划边界数据源（N03 行政区域 GML 448MB 太重；可换市政区 GeoJSON 轻量源）；
    ② 沿線**站序**（`seq`，巡店路线排序要用）——N02 的 RailroadSection 一段一 feature，
    要先按共享端点把段串成线再投影，属独立小工程；
    ③ 同名跨线车站**合并成 1 个任务**（`group_code` 已备好，等用户定合并规则）。
  - 迁移 **`aa11bb22cc33`**（新表 + `bd_station.place_id`）。⚠️ 踩坑：
    ① **选迁移 id 必须容错扫库**：老迁移里有 `revision: str = 'xxx'`（带类型注解）和单引号写法，
       只认 `revision = "xxx"` 的正则会漏 → 选到已占用的号 → `CycleDetected`（**同一天踩了两次**）；
       正确做法见 `/tmp` 里的教训：用 `^(?:revision|down_revision)(?:\s*:\s*[^=\n]+)?\s*=\s*['"]…['"]`
       扫全部 60 个文件（实测占用 59 个 id）。
    ② **SQLite 不能用 ALTER 加带外键的列**（"No support for ALTER of constraints"）→ 走 `batch_alter_table`；
       且 **batch 模式下外键必须显式命名**（否则 "Constraint must have a name"）。
    ③ 补丁脚本里 `assert` 失败会导致**前面的 replace 没落盘** → `create_station(line_id=)` 漏改而路由已在传它
       （界面新建车站必 500），是测试抓出来的。**改完要复查函数签名**。
  - 测试 `tests_web/test_team_task.py` **90 项**（+物理车站层归组/幂等/孤儿清理/体检抓脏数据/页面显示资产且
    无任务编号）；全量 **425 passed**（oauth e2e 那条是环境问题，干净树上同样失败）。

- **作业域重置（2026-10-05 用户："之前的 excel 文件导入的任务，全都可以删除。后面我们再做任务分配"
  + "测试队/测试2队可以清掉"）**：新流程确立 —— **车站数据资产 → 自动出任务 → Excel 只当派工单**。
  - ⚠️ **先纠正一个容易搞错的认知**：当初的 515 条**不是另一套数据**，导入时是**认领**（enrich）进资产的
    —— 它们就是 `bd_station` 里 id 1~515（`source='manual'`），N02 新增的是 id 516~1920，合计 1,920 = 全部资产。
    所以"清掉旧数据"要分两层：**车站资产层保留**（它就是要以之为准的那份），**任务层清空重建**。
  - 工具 `scripts/bd_reset_ops.py`（**默认 dry-run**，`--apply` 才做；自动备份库 + **把 bd_task 全量归档成
    `data/backup_tasks_<ts>.csv`**（utf-8-sig）+ 写 `bd_log(action='wipe')` 留痕）：
    `--tasks` 清任务层（progress/assign/task 三表）、`--teams "测试队,测试2队"` 清队（连带成员关系，
    那些人变自由人 + `sync_account_roles()` 把不再是队长的人退回 staff）。
  - 实测结果：任务 1,920 → **0**；队 8 → **6**（剩 小川/汤静/甘子杰/罗子傑/陈嘉溢/陳偉鋒）；
    12 名测试队成员释放为自由人；付罡 + DP-DEMO-01 两个原队长自动退回 `staff`；
    **车站资产原封不动**：站×线 1,920 / 物理车站 1,568 / 线路 131；一人多队检查 0 违规、资产体检健康 ✓。
    空任务状态下 `/tasks`（三 tab 全 0）、`/stations`（共 1,920 条）、`/teams`、`/logs`、`/messages` 都 200。
  - `bd_log.ACTION_LABELS` 补 `wipe`（清空重建）/`merge`（合并）。
  - ⓘ 清任务后 `bd_log(domain=task)` 的 1,405 条仍指向已删任务 id（**日志是追加表，不删**）→
    这是硬删的必然代价；要彻底干净需要另跑日志清理（未做，等用户定）。

- **沿線顺序（seq）：顺序**不自己算**，去 OSM 拿现成的（2026-10-05 用户："我觉得你应该还能找到
  其它的数据源来确定这个顺序，而不是计算出来…比如山手线的站点顺序" → 选 **A：OSM**，已接受 ODbL 署名）**：
  - **为什么能用 OSM**：route relation 的**成员本身就是有序的**（`role=stop`），**环线直接给一圈**
    （山手線 31 个成员 = 30 站 + 回到起点，还分内圈/外圈）；而 N02 只有"点 + 线"，**没有顺序字段**
    （Station 层就 7 个属性、RailroadSection 层就 4 个，实测确认），文件里的排列也不是顺序。
  - ⚠️ **关键发现：N02 的"线路" ≠ 管理员认知的"线路"**。N02 是**官方线路名**口径：
    `山手線` 只有 **17 站**（品川〜田端 的西北弧），因为东弧（東京〜田端）官方叫 **東北線**、
    南弧（東京〜品川）官方叫 **東海道線**；`埼京線`/`湘南新宿ライン`/`総武線快速` 在 N02 里**根本不存在**。
    OSM 给的是**运行系统口径**（山手線 = 30 站一圈）。→ 所以 `seq` 用 OSM 口径，**这是对的**，
    但要接受"同一条 N02 线路内 seq 可能跳号"（排序只看相对大小）。
  - **落地**：`bd_station` 加三列（用户："直接在现有的车站表里加一列，后面我们查的时候就拿这列做
    order 排序"）—— `seq`（1 起）/ `along_km`（沿线里程 km）/ `seq_src`（照的哪条 OSM 关系，可审计）。
    迁移 **`bb22cc33dd44`**（纯 add_column，SQLite 不用重建表）。
  - 工具两个：`scripts/bd_fetch_osm_routes.py`（拉 OSM → `scripts/bd_osm_routes.json`，**随仓库走**，
    导入不需要网络）+ `scripts/bd_fill_seq.py`（按**坐标**把 OSM 有序站对到我们的站，默认 dry-run）。
    实测：**495 条线路 / 8,489 个站次**；落到 **1,556/1,920 站（81%）、108/131 条线**；
    85 条线整条取齐、23 条部分、23 条没有（钢索/新交通/部分新幹線等 OSM 没有对应关系）。
  - **匹配三档**（`best_route_for_line`，同一档内**优先选覆盖我们站最多的班次**）：
    ① **F1 ≥ 0.70** 强匹配（绝大多数）；② **召回 ≥ 0.85 且名字相关** = 我们是它的一段
    （`山手線`：召回 0.88 / 精确 0.50 / F1 0.64，正是被①挡掉、必须靠②救的典型）；
    ③ 召回 ≥ 0.5 且名字相关 = 只能对上一部分（如实报告）。**再加"同名兜底"**：
    坐标差得多的站在已选中的线路里按站名匹配（限 2km，防同名异地）—— 山手線的
    `新宿`/`目黒` 就是这么补上的（N02 与 OSM 的站点坐标差 300m+）。
  - **踩过的坑（都花了时间）**：
    ① ⚠️ **绝对不能用 `out geom` 抓关系**：它会把整条线的几何（几百个点）一起返回 →
       40 条一批直接**静默截断**（527 个 id 只回来 195 个，缺的关系其实都好好的）；
       正确写法 `out body; >; out skel qt;`（只要成员节点）。
    ② Overpass 公开实例会**限流**（连续 429/504，三个镜像全挂）→ 改用
       **OSM 官方 API `relation/{id}/full.json`**（稳，还带成员标签=站名）；单条要几秒 →
       **4 并发 + 断点续传 + 每 20 条落盘**（527 条 ~5 分钟）。
    ③ 名字匹配的 `.*→.*` 贪婪式会把 `都営大江戸線 : 都庁前→光が丘` **整串吃掉** →
       核心词为空 → 所有线路被"名字不相关"误拒（覆盖率从 74% 掉到 52%）。改成
       先切 `[:：]`/`[-–—]` 再去后缀。
    ④ F1 只看"召回+精确"会**误配跨线班次**（实测 `多摩線` 被 `多摩快速急行` 抢走）→
       所以加了名字门 + 长度上限。
  - 界面：`/stations` 选了线路就**按 seq 排**（`list_stations` 里 `ORDER BY seq IS NULL, seq`），
    行首显示「顺序 + 里程」（如 `6 · 10.0km` = 葛西臨海公園）；页面底部按 ODbL 要求署名
    「线路顺序数据 © OpenStreetMap contributors」。
  - 用法：`./.venv/bin/python scripts/bd_fetch_osm_routes.py --source api`（重抓）→
    `DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/bd_fill_seq.py --apply`。
  - 测试 `tests_web/test_team_task.py` +3（OSM 文件有序且有许可 / 三档匹配与名字门 / 按线路查按 seq 排）；
    全量 **427 passed**。

- **几何法兜底（2026-10-05 用户："补上也行。补吧，不过别太勉强"）**：
  OSM 拿不到顺序的线路，用 **N02 的区间几何**兜底：`scripts/bd_seq_from_geometry.py`
  （把区间段串成一条路径 → 车站投影 → 沿線里程），产出 `scripts/bd_line_seq_geom.json`（16KB，随仓库走）。
  - **起点怎么定**：选**离我们的站最近的端点**（否则新幹線从新大阪起算，我们的站会显示 480km+）。
  - **质量门槛（"别太勉强"的落地）**：车站到轨道的**最大投影误差 ≤ 250m**，且**每个站都要贴得上**；
    串不全的线（有分叉/复线）放宽到"串起 ≥50% 段"但**仍要求每站贴得上**，否则**宁可空着并如实报出来**。
  - 实测：**20 条线拿到**（秩父本線 37 站 71.8km、横浜市営 1/3 号線、江ノ島電鉄、久留里線、いすみ線、
    シーサイドライン、大雄山線、御殿場線、新幹線 3 条、钢索/缆车…），**投影误差几乎全是 0.0m**；
    **3 条主动放弃**：`総武線`（1466m）、`成田線`（36938m，几何没接上）、`箱根登山 鉄道線`（3875m）。
  - **落库脚本的兜底规则**：OSM 没匹配上、**或只覆盖不到一半**的线路 → **整条改用几何顺序**
    （`seq_src='geom:N02'`）；**绝不混用两个源**（混了 seq 就不可比，排序会错）。
  - **最终覆盖率：1,747/1,920 站（91%）/ 128 条线**（OSM 1,556 站 + 几何 191 站）；
    仍没顺序的只有 3 条线（総武線 46 / 成田線 27 / 箱根登山鉄道線 11）。
  - ⚠️ 几何法**是兜底不是主源**：主源是 OSM（明文有序、含运行系统口径）。几何法是"自己算"，
    只配在 OSM 拿不到时用，而且要过质量门槛。
  - 用户口径："如果排序困难，那就算了…让管理员手工选择就好了。管理员肯定是清楚的" →
    **排序是增强，不是前提**：界面按线路筛出来的站，有顺序的按顺序+里程排，**没顺序的照样列出来可勾选**。
    **不走"从线路图片解析"那条路**（各家线路图样式不同、有版权、131 条要人工核对，比现有两个办法都贵）。
  - 输入是 N02 的 `N02-22_RailroadSection.geojson`（几何，不随仓库走；需要时 `scripts/bd_fetch_rail.py` 重新下载）。
  - 测试 +2（几何产物必须过质量门槛且**不包含那 3 条算不干净的线** / 落库脚本支持几何兜底）；全量 **429 passed**。

- **最后 3 条线：人工定稿顺序（2026-10-05 用户："剩下的，你可以通过 google 搜索来做。方式是笨点，
  但肯定能解决。"）**：
  - 对象 = OSM 和几何都拿不到顺序的 **総武線（46 站）/ 成田線（27 站）/ 箱根登山鉄道線（11 站）**。
  - 做法（**两个独立来源交叉校验**，不是单靠一个）：
    ① **Wikipedia「駅一覧」小节**（`?action=raw` 取原始 wikitext，**只抽駅一覧小节**）——
       ⚠️ 抽**全页**的链接顺序是错的（页面里到处都提到站名，抽到的是"首次出现"顺序，
       实测给出 `東京 → 銚子 → 小岩 → …` 这种乱序）；必须**只取駅一覧小节**。
       ⚠️ 还要跟重定向：`箱根登山鉄道鉄道線` 已重定向到 `小田急箱根鉄道線`（2022 年改名）。
    ② **坐标最近邻链**（从指定终点起：総武線=東京 / 成田線=佐倉 / 箱根登山=小田原）独立算一遍。
    两边**互相补漏**：Wikipedia 表头里的站抽不到（総武線的 平井/旭/松尾/榎戸、成田線的 小林/我孫子、
    箱根登山的 小田原/箱根湯本），坐标法正好给出它们的位置，两边一致。
  - 产物 `scripts/bd_line_seq_manual.json`（5KB）：每条线记 `source`（Wikipedia 条目）+ `note` + `km_mode`。
    **落库脚本的最后一级兜底**（`seq_src='manual:…'`，优先级：OSM → 几何 → 人工）。
  - **踩到的坑（写下来省下次）**：
    ① 総武線在 N02 里是**一条线**，但它其实含 **緩行線支线**（両国〜御茶ノ水）；
       按地理把支线放在**马喰町与錦糸町之间**（实测站间最大间距只有 5.6km，全段里程单调合理），
       这样"从东京往外"的顺序才说得通。
    ② 成田線是 **Y 字形**（本線 + 空港支線 + 我孫子支線）→ 线性列表**必然有一次大跳**
       （实测 松岸→空港第2ビル 37km）→ **`km_mode='none'`（不适用里程）**，只保证顺序；
       否则页面上会出现"成田空港 43km"这种误导数字。
    ③ 箱根登山有 **スイッチバック**，里程按站间直线累加会略小于实际（已在 note 里写明）。
  - **最终覆盖率：1,831/1,920 站（95%）· 131/131 条线都有顺序**
    （OSM 1,556 站/108 线 + 几何 191 站/20 线 + 人工 84 站/3 线）；
    剩下 89 站分布在那 16 条"只取到一部分"的线上（OSM 变体覆盖不全，可以后再补）。
  - 测试 +2（人工顺序文件必须自洽且**支线不适用里程** / 落库脚本支持 `manual:`）；全量 **431 passed**。

- **建任务（按线路选站；2026-10-05 用户定稿 + "你自己做吧，做完自己检查几遍"）**：
  - **任务概念（定稿，别再动摇）**：**1 个物理车站 = 1 个任务**（跨线站只 1 个；同名异地本来就 2 个）。
    用户原话："应该是 18 个任务…因为每个站对应一个任务，都有自己的状态。我分给 A 队的 10 个站，
    **并不是他这 10 个站作为一个整体任务跑完我再分新的**，而是在剩下几个站的时候，我就可以再派发
    新的一组任务给他。" → **派活是滚动的**，所以任务必须细到站、状态独立。
    （⚠️ 我中途一度按"任务=队×线路批次"设计，被用户纠正过；京葉線 10 站给 A + 8 站给 B = **18 个任务**）
  - 表：`bd_task` 加 **`place_id`**（FK `bd_station_place`）+ 部分唯一索引 `uq_bd_task_place`
    （`place_id IS NOT NULL` 上唯一）；`station_id` 改**可空**（老口径留空，`UNIQUE(station_id)`
    对 NULL 不生效 → 两种口径共存）。迁移 **`cc33dd44ee55`**；本地库直接重建 `bd_task`（当时是空表）。
  - 服务（`app/services/bd_tasks.py`）：
    - `list_places_for_line(db, line_id, kw)` —— 按线路列**物理车站**：`seq`/`along_km`（三级来源）、
      `lines_text`（还经过哪些线）、`has_task`（已有任务 → 页面禁用勾选）
    - `create_tasks_for_places(db, place_ids, team_id=None, ...)` —— 批量建（已有跳过并回报）、
      **分配日期自动今天**（用户："日期不用管，有个分配日期就行"；任务要跨好几天）、可选同时派队、
      写日志（create + dispatch 两条）
    - `place_ids_without_task` / `place_ids_for_stations` —— 车站页批量入口用（**车站 id → 物理车站 id**）
    - `_place_ids_taken(db)` —— **去重的唯一判据（两种口径都算）**：`bd_task.place_id` 直接命中
      **或** 老任务的 `station_id` 所属的 place。⚠️ 只算一边会出现"同一个车站两个任务"（真 bug，
      测试 `test_new_task_page_lists_places_in_line_order` 系列抓到后修的）
  - 页面 `/tasks/new`（`bd_task_new.html`）：**选线路（快速过滤）→ 该线车站按 `seq` 排列（带里程、
    还经过哪些线、已有任务禁用）→ 勾选 → 可选派队 → 建**。要点：
    - **线路只是筛子，不要求全选**（用户："我选择京叶线…我可以先建到葛西临海公园站"）
    - **勾选**：点一下勾；**Shift+点 = 勾上中间一整段**；**按住鼠标拖过多行 = 连续勾选**；
      全选/全不选按钮；只有一个都没勾时才提示（服务端也给提示）。手机端只有 tap（拖拽会和滚动打架，
      所以拖拽只走 mouse 事件，不绑 touch）
    - **一个页面不用分页**：最长的一条线（京急本線）50 站，一页放得下 → **勾选不会跨页丢失**
      （这正是"圈选队员"当初故意不分页的同一个坑）
    - 建完**留在本页**（方便连着建/换队再建）；提示"已新建 N 个（跳过 M 个已有）"
    - 入口：`/tasks` 右上角「建任务」+ `/stations` 选了线路后的「用这条线建任务」
    - **不做日期输入**（分配日期=今天，写死逻辑）；**不做担当选择**（队长分）
  - **任务视图改成"place 优先"**（否则新任务在页面上会消失）：`_rows` 同时取 place 与 station，
    `station_name`/`line` 两个键名**保持不变**（模板/导出不用改）但值取 `place` 优先；
    `_station_name()`（日志/消息用）；`_base_query` 的**线路筛选**改成 EXISTS（认 station_id 或
    place_id → 跨线站会出现在它经过的每条线的筛选里，这是对的）、**关键词**也加 place 的站名/线路文本。
  - 车站页那两个老批量入口（`/stations/tasks`、`/stations/dispatch`）**也改成按物理车站**
    （以前按站×线建 → 同一车站会建出两个任务）。
  - 测试 +7（页面按 seq 列 + 已有任务禁用 / 滚动派活 A队+B队=3 个任务 + 重复全跳过 / 页面提交后在
    已分配 tab 与线路筛选可见 / **老口径任务也算已有（回归）** / 车站页批量走 place / 详情页+导出+员工端
    渲染 / 只允许管理员）。全量 **438 passed**（仅 oauth 环境用例失败）。
  - ⓘ 数据小坑（不是 bug）：**西船橋**在京葉線上没有 `seq` —— OSM 的「京葉線 各駅停車（東京→蘇谷）」
    班次**不停西船橋**（它在支线上）。所以页面显示「—」但仍可勾选 —— 正好验证"没顺序也能用"的口径。


- **车站页重做（2026-10-06 用户："车站归车站，任务归任务，车站这边只是维护车站信息" +
  "你再看看车站的功能，缺失很多，我查都查不到" + "新建车站---不需要。车站不是我说建就建的"）**：
  - **两条口径**：① 车站页**只维护车站资产**，任务的东西全部移走；② **不提供新建车站**
    （车站是官方数据资产，由 `scripts/bd_import_rail.py` 从 N02 导入 / 脚本维护，
    `create_station()` 服务函数**保留**给脚本与测试用，只是**界面上没有入口**）。
  - **实测"查不到"的原因（修之前）**：关键词只搜 `bd_station.name` + `line` 两个文本列 →
    搜 `JR東日本`、`都営`、`千葉県`、`003785`（駅コード）**全部 0 命中**；
    而且默认按 `id` 排序（1,920 行 / 39 页，毫无规律）。
  - **修后（一个框搜全部 + 四个筛选 + 可控排序/分页）**：
    - **关键词**：站名 / **駅コード** / **运营商** / 线路名 / **都道府県名** / 还经过哪些线
      （`bd_station.name|line|ekicode|operator` + `bd_line.name|operator_short` +
      `bd_station_place.lines_text` + `PREF_LABELS` 反查县名 → 命中 `pref` 代码）
    - **筛选**：线路（131 条）/ **都道府県** / **运营公司** / **线路类型** / 状态 / **只看跨线站**
      （`bd_station_place.n_line > 1`）；选项由 `bd_tasks.station_facets(db)` 从数据现取
    - **排序**（`STATION_SORTS`）：**线路 + 顺序**（默认，`operator_short, line, seq`）/
      站名 / 都道府県 / 駅コード；**每页 20/50/100**
    - **导出 CSV**（`/stations/export`，UTF-8 BOM）：**跟着当前筛选**导全量
  - 表格列（资产视角）：顺序+里程 / 车站（跨线站标「N 条线」）/ 线路（+类型）/
    **駅コード** / **都道府県** / **物理车站（经过哪些线）** / 状态 / 编辑。
    ⚠️ **不显示任何内部 id**（物理车站 id 也不露，用户口径"编号不用显示"）。
  - **移走的东西**：任务列（所属团队/任务状态/进度）、「还没建任务」筛选、批量建任务/批量派队两个表单、
    「新建车站」卡片与 `POST /stations/create`（**路由已删**）；`POST /stations/tasks`（批量建任务）、
    `POST /stations/team`（批量派队）也**整块删掉**（用户 2026-10-06："批量建任务也不需要。
    这个页面只维护车站信息"）→ 现在 `/stations` 只剩 GET 列表 / GET 导出 / POST 编辑三条路由
    （删掉的路由被访问是 **404**）。
    - 服务层保留（**脚本与导入器还在用**）：`create_station`（脚本/测试建站）、`create_tasks`（车站级建任务，
      `bd_seed_team_task.py`/`bd_import_rail.py` 在用）、`place_ids_without_task`/`place_ids_for_stations`
      （车站→物理车站映射，**已修：文档说去重，实现却返回重复 place**）。
    - 批量建任务的**正式入口 = `/tasks/new`**（按线路选站），车站页不再承担任何任务操作。
  - ⚠️ **导出静默截断（真 bug，2026-10-06 实测抓到）**：`paging.paginate` 把 `per` 夹到
    `PER_MAX=200` → 导出若走分页**只导 200 行**（千葉県 383 条只导出 200 条）。
    修法：`list_stations(all_rows=True)` 走**不分页**路径；回归测试
    `test_stations_export_is_not_truncated`（250 个站必须导出 251 行）。
    与 `/stores/entities` 的 `limit(500)` 是同一类"看着成功、实际丢数据"的 bug。
  - 测试 +4（关键词覆盖运营商/駅コード/县名、四个筛选+四种排序、跨线站筛选、导出全量）；
    全量 `tests_web` **442 passed**；模板 0 错；i18n 0 缺失 0 死键（顺带清掉 14 个死键）。

- **任务页布局重排（2026-10-06 用户："未分配/已分配/已完成下边**直接**显示任务/车站信息。
  对于团队的任务信息**往下放**。你现在这么放，一没逻辑，二没规则" +
  "我怎么看每个队的任务信息，包括已完成，未完成"）**：
  - **问题**：原来顺序是 `tab → 按队汇总表 → 任务列表` —— 汇总把主体挤到下面，看 tab 点完看不到任务。
  - **新顺序（固定，别再动）**：`页面标题/操作 → 概览统计卡 → 筛选卡 → **三个 tab** →
    **任务列表（表格 + 分页）** → **按队汇总（在下面）**`。
    同时删掉那句多余的「共 N 条（本页 M）」（分页器里本来就有「共 N 条 · 第 x/y 页」）。
  - **按队汇总升级成可钻取的真表格**（`data-testid="by-team-card"`）：
    列 = 队伍 / 任务 / **已完成** / **进行中** / **未分配** / 停滞 / **完成率**；
    **每个数字都是链**（`/tasks?team=<id>`、`&tab=done|doing|unassigned`、`&stale=1`）——
    这就是"看每个队的任务信息（含已完成/未完成）"的正式入口；队名链到该队全部。
    数据来自 `team_board_summary()`（一次查询后在 Python 聚合，含 `team_id` 供链接用）。
  - ⚠️ 口径提醒：**「停滞」= 未完成 且（从没提交 或 ≥2 天没动）** → 刚派下去、一个人都还没动的队，
    停滞数会等于未分配数（这是"谁没动"的抓手，不是 bug）。
  - 守门测试 `test_tasks_page_layout_tabs_list_then_team_summary`：**断言页面里三块的字节顺序**
    `tab < task-row < by-team-card`，并要求按队汇总有 总数/已完成/进行中/未分配/完成率 且能钻取。
  - 测试 `tests_web/test_team_task.py` **109 项**；全量 `tests_web` **443 passed**。

- **中日字形归一（搜索；2026-10-06 用户："京叶线为什么不够呢？我输入的是中文，是不是这里有问题？
  是不是这些线路都加个中文名？你觉得要怎么做才合适" + "车站的搜索功能，你要多测试几遍"）**：
  - **现象（实测，修前）**：数据是**日文新字体**、界面是中文 → 用户输中文**全部 0 命中**：
    京叶线/东京/涩谷/海滨幕张/东横线/半藏门线/樱木町/台场 = 0；日文形（京葉線 18 站/東京 74/渋谷 8…）正常。
  - **方案：不做"每条线加中文名"的字段**，改成**一层字形归一**（`app/services/bd_cjk.py`）。
    理由：131 条线 + 1,920 站加一列中文名 = 数据维护两份、必然不同步；根因是**字形**（日文新字体 ↔ 简体/繁体），
    属算法问题，归一一次**全线受益**（站名/线路/运营商/都道府県/駅コード）。要**显示**中文名时用 `line_zh()/to_zh()`
    **现算、不落库**。
  - 三个东西：
    1. `to_jp(s)`：简体/繁体 → 日文新字体（**搜索前把用户输入转成数据的形态**）+ **整串别名**（`ZH2JP_ALIAS`，
       处理汉字映射管不到的：丸之内线→丸ノ内線、小机→小机、小湊铁道线→小湊鐵道線）
    2. `to_zh(s)` / `line_zh(name)`：日文 → 中文（**显示用**；反向表用 `setdefault` 保证**简体优先**，
       否则会显示成"澀谷"）
    3. `search_variants(q)` = 原串 + 日文形 + 中文形；`zh_line_ids(db, q)` = 中文线路名 → 线路 id
  - **接进 3 处搜索**（车站页 `/stations`、任务页 `/tasks`、建任务页 `/tasks/new`）：关键词按变体 OR；
    线路名额外用 `zh_line_ids` 反查（解决「丸之内线」↔「丸ノ内線」）。
  - **线路下拉 = 可搜索 combobox**（`data-cb-filter`，复用 `emp_select.js`）：每个 option 挂
    `data-zh="{{ line_zh(ln.name) }}"`（服务端现算）→ JS 匹配"显示文本 + data-zh"，
    **前端不再抄一份映射表**（前后端同源）。
  - **数据驱动自检**（关键做法）：拿**库里全部 131 条线 + 1,920 个站名**做"中文形 → 日文形"往返校验
    → 抓出 5 个边界（小机/町屋駅前/王子駅前/大塚駅前/小湊鐵道線）并逐个修 → **0 个转不回来**。
    ⚠️ 往返校验抓不到"漏映射"（`to_zh` 原样返回时往返也成立）→ 必须再跑**中文查询矩阵**
    （实测 43 个关键词：中文/日文/繁体/部分/駅コード/运营商/都道府県）。
  - **顺手抓到的两个真问题**：
    ① **任务页跨 tab 陷阱**：在「未分配」tab 搜「涩谷」→ 0 行（任务在「已分配」），看起来像搜索坏了
       → 加 `data-testid="other-tabs-hint"`：当前栏没匹配时提示"别的栏里有 N 个"并给切换链接。
    ② **place 任务的运营商/线路名搜不到**（`bd_task.station_id` 是 NULL，老口径经 station_id join）
       → 实测 `/tasks` 搜「JR東日本」= 0；修法：再按"该物理车站被哪些线经过"查一遍（回归测试锁住）。
  - 测试 +5（字形助手 / 车站页中文矩阵 / 线路下拉可搜索+data-zh / 跨 tab 提示 / place 任务运营商搜索）；
    全量 `tests_web` **447 passed**；模板 0 错；i18n 0 缺失 0 死键。映射对 **844**。

- **驳回（2026-10-06 用户："可以退回的，比如队员报了100%，队长可以驳回，队长确认了以后，
  管理员可以驳回。驳回就是把100%的进度改成不到100%"）**：
  - **判权唯一入口 `bd_tasks.can_reject`**：队员报了 100%（`reported_pct==100` 且
    `review_status=='pending'`）→ **该任务的队长**可以驳回；**队长确认之后 → 只有管理员**能驳回
    （队长不能再改回，避免"自己确认自己驳回"）；员工不能驳回。
  - **动作 `bd_tasks.reject_progress(task_id, pct, note)`**：
    - 只对 `pct == 100` 的任务有效；**新进度必须 0–99**（改成 100 会被拒）
    - 写**今天**这条进展（当天有则覆盖），`review_status='rejected'`，
      ⚠️ **员工上报的原值 `reported_pct` 保留**（界面上能看到"队员报 100% → 被驳回改 80%"）
    - **回退清完成日**（`done_date=None`）+ 状态回到进行中（与"进度回退"的既有口径一致）
    - 写 `bd_log`（action=`reject`，old `100%` → new `NN%`）+ **给担当发消息**
      （`bd_msg.notify_task_progress(action='rejected')`，文案含原值→新值与操作人）
  - **路由 `POST /my/tasks/reject`**（`task_id` 走表单，与 `/my/tasks/confirm` 一致）：
    ⚠️ **必须挂在 `/my/` 下** —— **队长被中间件挡在 `/tasks/*` 外**（他只能在 `/my/*` 操作），
    管理端页面也提交到这里；判权在路由里用 `can_reject` 再查一次（不信任界面）。
  - **界面**：管理端 `/tasks` 的**已完成**行的操作列从"修正进展"换成**「驳回」**（数字默认 90 + 原因）；
    队长端 `/my/tasks` 在**待确认** tab 给同样的驳回表单（`data-testid="reject-form-<id>"`）。
  - 测试 +4（判权矩阵 leader→admin 两段 / 驳回后 pct<100 + 清完成日 + 原值保留 + 不能驳成 100 /
    路由权限（员工被中间件挡、队长能驳回）/ 两个页面都有驳回表单）。
  - ⚠️ 踩坑记录：`save_progress` **自己不 commit**（由路由 commit）—— 测试里直接调服务必须自己 `db.commit()`；
    `_login` 的默认口令是 `pw123456`（不是 demo123）。

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
