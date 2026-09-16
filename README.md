# 巡店数据结算系统（store_system）

面向巡店业务的**数据结算 Web 系统**：从原始巡店 Excel 到员工薪资结算的完整闭环——文件导入 → 自动判定 → 申诉/绩效确认 → 正式表 → 绩效工资（月内两期发薪）→ 月度对账（归因 + AI 分析）→ 薪资找平（找平执行 / 上月余量递延）。

生产环境：https://store.visitworld.me

## 功能特性

- **巡店文件处理**：多分卷上传、自动清洗判定（有效 / 同店跨日 / 从档 / 重复导入 / 空白，逐行写明原因）、跨文件判重（同店同月取最早）、店铺主/从档归并
- **申诉与绩效确认**：员工对被滤记录申诉，管理员认可/驳回，全程留痕；存在未决申诉的文件不能入正式表
- **绩效与工资**：月绩效物化；**月内两期发薪**（上半月 1-15 · 20 日发 / 下半月 16-月末 · 次月 5 日发）；发薪表按「总金额 / 找平金额 / 应该付金额」照表发薪；单价按月可配
- **月度对账**：上传对账文件（自动识别表头，必要时 AI）、按员工×天比对、差异自动归因、反向名单、AI 大白话分析、结果 Excel 下载、同月重传版本化
- **薪资找平**：偏差（系统参考，自动刷）与找平（人工执行，增量累计）双轨；上月未找平余量 × 上月单价自动递延下月；一键全找平
- **权限与安全**：管理员/员工角色隔离（中间件 + 路由双层）、申诉归属校验、CSRF、强制改密
- **Agent 友好**：页面注入 `window.__PAGE_STATE__`（结构化状态）与 `data-testid`，浏览器自动化可直接读取，无需截图 OCR

## 技术栈

FastAPI · SQLAlchemy 2 · Alembic · Jinja2 · htmx · openpyxl · PostgreSQL（生产）/ SQLite（本地）

## 快速开始

```bash
# 依赖
./.venv/bin/pip install -r requirements.txt -r requirements-web.txt

# 本地配置（AI key 等，见 app/config.py 环境变量清单）
cp .env.example .env

# 启动本地服务（自动加载 .env）
./scripts/dev_server.sh

# 运行测试
./.venv/bin/python -m pytest tests tests_web -q
```

默认账号：管理员 `admin`（密码见环境变量 `ADMIN_PASSWORD`；本地默认 `demo123`）。

## 核心业务规则

| 规则 | 说明 |
|---|---|
| 工资 | 每点 × 当月单价（默认 250円，按月可配 per_point） |
| 奖金 | **每满 68 点奖 3,000円**（整月滚动、不跨月；上半月余数带向下半月；门槛/奖额由 `BONUS_GROUP`/`BONUS_AMOUNT` 配置） |
| 判重 | 窗口 = 结算月；键 = (店名 trim, 月)；组内最早时间戳行有效；跨月不互压 |
| 对账 | 系统正式表 vs 对账文件按人比对；差异归因（一致/仅系统有/仅对账有） |
| 找平 | 偏差 = 系统参考值（自动刷）；找平 = 人工执行值（输入框 = 剩余 = 偏差−已找平，保存为增量累计）；上月修正 = 上月未找平余量 × 上月单价 递延 |

## 目录结构

```
app/
  routers/     # 路由（auth/files/v3/perf/stores/accounts/info）
  services/    # 业务逻辑（v3_flow 判定入表 / v3_perf 绩效工资 / v3_period 薪资找平 / v3_recon 对账 / importer 导入 / ai_chat AI）
  templates/   # Jinja2 页面（v3_perf 发薪表 / payroll_settle 找平 / v3_recon 对账等）
  models.py    # SQLAlchemy 模型
  config.py    # 环境变量配置（含奖金规则配置项）
store_settle/  # 核心引擎：Excel 解析 / 判重 / 点数规则（纯 Python 可复用）
migrations/    # Alembic 迁移
scripts/       # 运维脚本（dev_server 本地服务 / reset_* 数据重置 / restore 恢复）
tests/ tests_web/  # 单元测试（引擎 + Web 流程/接口）
docs/          # 产品说明 / 使用手册 / 技术方案 / 测试用例 / 索引
```

## 文档

- `docs/产品设计-系统逻辑全览.md`：业务逻辑与设计全览
- `docs/技术方案.md`：系统架构与技术决策
- `docs/测试用例.md`：测试用例（场景 / 后端接口 / 前端可用性三类）
- `docs/验证清单.md`：QA 走查清单
- `docs/索引.md`：模块速查（改代码先看这里）

## 部署

Docker Compose（web + PostgreSQL）+ Nginx。发布：rsync 新 release → 复制 `.env` → 切换 symlink → `docker compose build/up`（entrypoint 自动 `alembic upgrade head`）。数据变更前先 `pg_dump` 备份。
