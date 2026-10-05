# 巡店结算系统 — Agent 冷启动手册（入口 · 保持短）

> **先读本文件（约 18KB）。然后只读你要改的那个功能的记录文档与源码**——
> 别把 `docs/` 整套读进来（那是 300KB+，纯浪费）。
> 历史实施记录已按功能拆到 `docs/记录-*.md`：**只有改到该功能时才读那一个文件**。

## 怎么用这份手册（省 token 的关键）

1. 在下表找到要改的功能 → 只读那一行列出的**记录文档 + 源码文件**。
2. 不知道文件在哪 → `./.venv/bin/python scripts/idx.py <关键词>`（查 `docs/文件索引.tsv`，全仓 400+ 文件）。
3. 改完代码：更新**对应的记录文档**（别往本文件堆细节）+ `docs/文件索引.tsv`（新增/改名/换职责时）。

## 功能 → 读什么

| 功能 | 记录文档（按需读，**别全读**） | 主要源码 | 测试 |
|---|---|---|---|
| 结算主流程（文件→判定→申诉→正式表→点/工资） | `docs/结算主流程设计-v3.md`、`docs/产品设计-系统逻辑全览.md` | `app/services/{flow,perf,formal,payroll,period}.py` | `tests_web/test_*.py` |
| 月度对账 / 薪资找平 | 同上 | `app/services/{recon,payroll_adjust}.py` | `tests_web/test_recon*.py` |
| 员工每日填报 + 对比 + AI 报告 | **`docs/记录-员工填报.md`** | `app/services/{daily_report,report_compare,report_ai,report_export}.py` | `tests_web/test_daily_report.py`、`test_report_robustness.py` |
| 日期计划（半月出勤登记） | **`docs/记录-日期计划.md`** | `app/services/{date_plan,plan_leave}.py` | `tests_web/test_date_plan.py` |
| **团队 / 车站 / 任务（作业域）** | **`docs/记录-作业域.md`**（85KB，只改这块时才读） | `app/services/{bd_teams,bd_tasks,bd_lines,bd_places,bd_leave,bd_msg,bd_perm,bd_assign_ai,bd_log,bd_cjk}.py` | `tests_web/test_team_task.py` |
| 站内消息 | `docs/记录-作业域.md` §消息、`docs/specs-messages.md` | `app/services/bd_msg.py` | 同上 |
| MCP 服务（WorkBuddy 接入） | `docs/MCP对接手册.md`、`docs/specs-mcp-tools-scenario.md` | `mcp_service/**` | `mcp_service/tests` |
| 多语言 / 导航 / H5 | 本文件 §已知坑 + `docs/索引.md` | `app/i18n.py`、`app/templates/base.html`、`app/static/app.css` | `tests_web/test_templates.py`、`test_i18n.py` |
| 部署 / 发布 | 本文件 §发布流程 | `scripts/deploy*.sh`、`deploy/**` | — |

**关键约定（改任何功能都要守）**：
- 路由 `app/routers/*.py`（页面 + 写端点）、服务层 `app/services/*.py`（业务逻辑唯一来源）、
  模板 `app/templates/*.html`、迁移 `migrations/versions/*.py`。
- 新增表单必须放 `{{ form_token() }}` + `csrf_token`；表格 `class="grid-tbl"` 且外包 `.tbl-wrap`；
  文案用 `t('中文')`（i18n 键**不能含双引号**，引用词用「」）。
- 改完跑：`scripts/check_templates.py`、`scripts/i18n_audit.py`（要 0 缺失 0 死键）、相关 `pytest`。


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

## 文件地图（省 token 用）

| 文件 | 作用 | 什么时候读 |
|---|---|---|
| `docs/文件索引.tsv` | **全仓文件的索引**（路径 / 功能域 / 一句话 / 关键入口） | 找文件时先查它（或 `scripts/idx.py 关键词`） |
| `docs/索引.md` | 模块速查（路由 / 服务层 / 模型 / 测试 / 部署） | 需要跨模块看时 |
| `docs/记录-作业域.md` | 团队/车站/任务的历史口径、踩坑、实测数据（85KB） | **只改作业域时** |
| `docs/记录-员工填报.md` | 每日填报/对比/AI 报告的历史记录 | 只改这块时 |
| `docs/记录-日期计划.md` | 出勤登记的历史记录 | 只改这块时 |
| `docs/specs-*.md` | 各期的设计规格（team/station-task/date-plan/bd-ops/messages/MCP） | 需要原始设计意图时 |
