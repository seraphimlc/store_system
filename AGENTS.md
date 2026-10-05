# 巡店结算系统 — Agent 冷启动手册（入口 · 保持短）

> **只读你要改的那块**：本文件（入口）→ 下表对应行的记录文档 → 那几个源码文件。
> 别把 `docs/` 整套读进来（300KB+，纯浪费）。文档地图见 `docs/README.md`。
> 找文件：`./.venv/bin/python scripts/idx.py <关键词>`；改完更新对应记录文档（别往本文件堆细节）。

## 功能 → 读什么

| 功能 | 记录 / 规格（按需） | 主要源码 | 测试 |
|---|---|---|---|
| 结算主流程（判定→申诉→正式表→点/工资） | `docs/结算主流程设计-v3.md`、`docs/技术方案.md` | `app/services/{flow,perf,formal,payroll,period}.py` | `tests_web/test_*.py` |
| 月度对账 / 薪资找平 | `docs/产品设计-系统逻辑全览.md` | `app/services/{recon,payroll_adjust}.py` | `tests_web/test_recon*.py` |
| 员工每日填报 + 对比 + AI 报告 | **`docs/记录-员工填报.md`** | `app/services/{daily_report,report_compare,report_ai,report_export}.py` | `test_daily_report.py`、`test_report_robustness.py` |
| 日期计划（半月出勤登记） | **`docs/记录-日期计划.md`** | `app/services/{date_plan,plan_leave}.py` | `test_date_plan.py` |
| **团队 / 车站 / 任务（作业域）** | **`docs/记录-作业域.md`**（只改这块才读） | `app/services/bd_*.py` | `test_team_task.py` |
| 站内消息 | `docs/记录-作业域.md` §消息、`docs/specs-messages.md` | `app/services/bd_msg.py` | 同上 |
| MCP 服务（WorkBuddy 接入） | `docs/MCP对接手册.md`、`docs/specs-mcp-*.md` | `mcp_service/**` | `mcp_service/tests` |
| 多语言 / 导航 / H5 | `app/i18n.py`、`app/templates/base.html`、`app/static/app.css` | — | `test_templates.py`、`test_i18n.py` |
| 部署 / 发布 | §发布流程 + `deploy/README.md` | `scripts/deploy*.sh`、`deploy/**` | — |

## 常用命令（cwd = 项目根）

```bash
./.venv/bin/python -m pytest tests_web/test_xxx.py -q     # 单测（小改只跑相关）
./.venv/bin/python -m pytest tests tests_web -q           # 全量（约 2 分钟）
./scripts/dev_server.sh                                    # 本地服务（自动加载 .env 含 AI key）
DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/xxx.py
./.venv/bin/python scripts/idx.py 车站 任务                 # 按关键词找文件
./.venv/bin/python scripts/check_templates.py              # 模板结构自检（改完模板跑）
./.venv/bin/python scripts/i18n_audit.py                   # 日文缺失/死键（要 0/0）
scripts/mcp_restart.sh                                     # 改完 mcp_service 必须重启（自校验 16 工具）
```

- 账号：`admin/demo123`；员工 `demo123`（测试里是 `pw123456`）。线上 `store.visitworld.me`。
- **heredoc `python3 <<EOF` 偶发静默失败**（尤其中文/反引号）→ 一律写 `scripts/*.py` 或 `/tmp/*.py` 再执行。
- AI：`.env` 的 `AI_API_KEY/AI_BASE_URL/AI_MODEL`；`ai_chat.chat()` 自动重试 2 次，**默认直连**（`AI_PROXY` 才走代理）。
- MCP：独立 venv（`mcp_service/.venv`，Py3.12）；启动前 **`DATABASE_URL` 必须显式设置**
  （`app/db.py` import 时固化缓存，不设会静默连空库）。细节见 `docs/MCP对接手册.md`。

## 铁律

1. **结算域与作业域分开**：作业域只写 `bd_*` 表，**绝不向结算域四表加列**
   （`formal_records`/`person_daily_stats`/`month_perf_records`/`payroll_period_rows`），结算域永不读作业域。
   守门测试：`test_station_tasks_does_not_touch_settlement_tables` /
   `test_settlement_code_never_reads_ops_domain` / `test_progress_submission_only_writes_bd_tables`。
2. **不读 `raw_records`**（隐私）；`data/uploads/` 与线上数据不进仓库。
3. **业务逻辑只在服务层**（`app/services/`）；路由只做鉴权+取参+渲染，模板不含口径判断。
4. **别跨 session 传 ORM 对象**（中间件 session 已关闭 → 对象 detach → 路由改属性再 commit
   **静默不生效**，实测把"改密码"改坏了）；中间件要数据就传列值快照。
5. **列表 SQL 要常数条**，别在循环里调 `can_*`（实测 76 行 = 188 条 SQL）；批量权限用
   `bd_tasks.can_reject_maps`。
6. 新增 POST 表单必须 `{{ form_token() }}` + `csrf_token`；表格 `class="grid-tbl"` + 外层 `.tbl-wrap`；
   文案 `t('中文')`（i18n 键**不能含双引号**，引用词用「」）。

## 关键基准（系统实测，非手工文件）

> 2026-09-25 用户明确：手工基准文件已作废删除。验收 = **与同一份库的 SQL 直查一致**，不钉死数字。

| 项 | 2026-08 | 2026-09 |
|---|---|---|
| 正式表店数 | 12507 | 15070 |
| 1点 / 2点 | 8199 / 4308 | 10663 / 4407 |
| 总点数 | 16787 | 19477 |
| 工资（円） | 4,895,750 | 5,173,000 |

- **判重口径**：窗口=结算月，键=(店名 trim,月)；组内取 ① deploy=YES（→2点）② 非 AUDIT_FAILED（→1点）
  ③ 纯 AUDIT_FAILED 无投放（→0点，不入正式表）；同级取 modified 最早；跨月不互压。
- **工资**：每点 250 円（可配），每满门槛点奖 3000（默认 68，`BONUS_GROUP_SCHEDULE` 按月可配）。
- **找平**：偏差 = 对账金额 − 系统已发（负=扣/正=补），自动写表；发薪时上期扣补→下期→递延下月。
- **员工可见起始月**：`staff_visible_from`（/config 页），未配置回退 env（默认 `2026-10`）。
- **H5**：≤860px 顶栏折叠为汉堡；宽表 `.tbl-wrap` 横滑，≤760px 首列吸附。
- **多语言**：`t('中文')` + `app/i18n.py`；解析顺序 `?lang=` → cookie → 账号 `User.lang` → 浏览器 → zh；
  模板循环变量**不可用 `t`** 命名（会覆盖全局 `t()`）。

## 发布流程（生产 = 新机，ssh 别名 store-prod）

1. 本地全量测试过 → commit → `git push origin main`。
2. `TS=$(date +%Y%m%d_%H%M%S)`；`ssh store-prod "mkdir -p /opt/store-settle/releases/$TS"`；
   `rsync -a --delete -e ssh --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' --exclude 'data/' --exclude '.pytest_cache' --exclude '.DS_Store' ./ store-prod:/opt/store-settle/releases/$TS/`
3. `ssh store-prod "cp /opt/store-settle/current/deploy/.env /opt/store-settle/releases/$TS/deploy/.env; rm -f /opt/store-settle/current; ln -s /opt/store-settle/releases/$TS /opt/store-settle/current"`
4. `ssh store-prod "cd /opt/store-settle/current/deploy && docker compose build web && docker compose up -d --no-deps web"`（entrypoint 自动 `alembic upgrade`）。
5. 公网走查（健康/绩效/找平/对账/产品页）。改数据前先 `pg_dump` 备份。
6. 旧机（`ssh store-old`）只留数据供回滚，不参与发布。

## 已知坑（踩过的，别重踩）

- **店铺主档自动化**：上传后台自动 建候选对 → 合并 exact → 受影响月重算；历史坑是"候选对从未生成"
  → 同店不同写法各算一家（8 月多 4 条）。测试 `tests_web/test_store_auto.py`。
- **找平债务跨月**：某人某月无工资行时，结转必须从**找平表** `payroll_adjusts.remaining` 读，
  只读"上月找平行"会丢债务（实测 11 人 / -89,750 收不回）。
- **薪资四表**：`payroll_period_rows`（应发，可重算）/ `payroll_payments`（实发台账，不可改写，
  **导出发薪表即登记**）/ `payroll_adjusts`（进度）/ `payroll_settlement_links`（发放↔找平）。
- **数据自洽**：`scripts/verify_payroll_logic.py`（或 MCP `visit_verify`）8 项互证；对线上用 `scripts/compare_with_prod.py`。
- **迁移必须真跑**：`tests_web/test_migrations.py` 在临时库 `alembic upgrade head` 并核对 ORM 列；
  本地库不跑 alembic（`create_all` + 手工 `ALTER`），新增表/列两边都要补。
- **导出文件名含中文**：`filename=` 必须 ASCII、中文走 `filename*`（否则响应头 latin-1 报错）。
- **i18n 键不能含 `"`**（会破坏 `app/i18n.py` 字典 → 页面 500）；引用词用「」。
- MySQL TEXT 默认值要 `sa.text("('')")`，唯一键含 TEXT 用 VARCHAR(255)。
- 线上演示期间别做写操作（删文件/重算/重传对账/重置口令）。

## 文档地图

| 文件 | 作用 |
|---|---|
| `docs/README.md` | **文档地图**（什么情况读哪份） |
| `docs/文件索引.tsv` | 全仓 400+ 文件的索引（`scripts/idx.py` 查） |
| `docs/索引.md` | 跨模块速查表（路由 / 服务层 / 模型 / 测试 / 部署） |
| `docs/specs-*.md` | 各期设计规格（团队/车站任务/日期计划/作业域/消息/MCP） |
| `docs/记录-*.md` | 按功能拆的**实施记录**（口径、踩坑、实测数据）——只读要改的那一个 |
| `docs/archive/` | 历史过程稿（**别当现状读**） |
