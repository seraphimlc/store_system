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
```
- 账号：`admin/demo123`；员工 `demo123`。**heredoc `python3 <<EOF` 偶发静默失败 → 一律写 scripts/*.py 文件执行**。
- AI：本地 .env（AI_API_KEY/AI_BASE_URL/AI_MODEL=deepseek-v4-flash）；线上 deploy/.env。ai_chat.chat 自动重试 2 次。

## 关键基准（演示/验收必须一致）
| 项 | 8月 | 7月 |
|---|---|---|
| 正式表 | 12511 | 12958 |
| 1点/2点 | 8231/4280 | 9760/3198 |
| 总点数 | 16791 | 16156 |
| 工资 | 随奖金规则（每满68点奖3000） | — |
| 员工 | 34 | 22 |

- **工资规则**：每点 250円（按月可配 per_point），奖金**每满门槛点奖 3000**（整月滚动、不跨月；门槛按月可配：默认 68，`BONUS_GROUP_SCHEDULE` 如 `2026-09=75`）；2点成功率 37% 仅展示。
- **判重**：窗口=结算月；键=(店名trim,月)；组内最早全时间戳行 valid；跨月不互压。
- **申诉**：master_late/from_sub 可申诉，其余滤除不可申诉；默认全部认可；存在 pending 申诉的文件不能入正式表。
- **对账**：person_daily_stats 为本地侧；recon_day_rows 只存问题行；同月重传→旧任务标「上一版」、新任务当前；person_points(人月汇总)对账写全量 ReconDataRow。
- **找平（金额制·自动）**：偏差金额 = 对账金额 − 系统已发（**负=扣款/正=补款**，含奖金），**自动写表**（`adjust_amount=diff_amount`，无需点击、页面只读）；发薪时**上半月扣/补 → 不够转下半月 → 两期都不够递延下月**（链式 `prev_adjust_amount`：本月结转 = diff + 本月两期吸收上月结转后的剩余）。8 月封账数据不随规则变更。

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
- MySQL TEXT 默认值需 `sa.text("('')")`；唯一键含 TEXT 列用 VARCHAR(255)。
- store_entities 自引用外键中间态需按 dialect 禁用触发器/FK 检查。
- 演示库正式表只应保留 7/8 月；9 月真实文件到了用「月度重算」重建。
- 线上演示期间别做写操作（删文件/重算/重传对账/重置口令）。
- 权限：员工访问管理页被中间件+路由双层拦截；申诉有归属校验；管理路由已补 role!=admin（纵深防御）。
