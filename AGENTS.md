# 巡店系统 — 当前项目入口

## 目录与状态

- **当前系统**：`apps/admin/`（React 19 + Appica）、`apps/staff/`（React 19 + Ant Design Mobile）、`apps/server/`（NestJS API）、`apps/worker/`（任务）、`packages/`（共享逻辑/契约/客户端）。
- **旧项目集中在 `legacy/`**：Python `app/`、`store_settle/`、`migrations/`、Python测试、`mcp_service/`、旧 `deploy/`、Python/Shell脚本和脚本资料。完整历史操作手册见 `legacy/AGENTS.md`，旧说明见 `legacy/README.md`。
- `docs/` 保留共享业务规格与交接记录；历史文档里的 `app/...`、`tests_web/...`、旧 `deploy/...`、Python脚本路径均相对于 `legacy/`。不要把旧发布流程套用到新系统。
- 根目录 `.env`、`.env.next.local`、`.env.next.docker.local`、`.venv/`、`data/`、数据库文件和 `.release/` 是现有本地配置/运行资产，本次收敛没有移动、覆盖或迁移。
- 当前状态先读 `docs/交接-当前状态.md`，文档地图 `docs/README.md`；仅按需读相关记录，修改后更新对应记录。

## 开发、验收与发布

- `pnpm dev`：管理端8001、员工端8002、API3000及Worker。开发连接 `store_settle_refactor_p1`；保留用户现有配置。
- 本地Docker统一入口8000（管理端 `/admin/`，员工端 `/`），显式连接基准库 `store_settle`。部署见 `deploy-next/README.md`，使用独立 `.env.next.docker.local` 的 `NEXT_DOCKER_DATABASE_URL`，不能修改开发连接。
- 当前模块检查：`pnpm --filter @store/admin typecheck` / `pnpm --filter @store/staff typecheck` / `pnpm --filter @store/server typecheck`；构建用对应模块 `build`。
- `pnpm test` 使用已有隔离运行器，PostgreSQL写测试只在随机schema-only临时库执行；不得向基准库或开发完整副本写fixture、清表或覆盖密码。
- 浏览器验收使用 `scripts/test-isolated.mjs --browser` 的相关分组；只重跑受本次变更影响的检查。静态文案/样式不新增业务测试。
- 新栈Python对照调用通过 `scripts/legacy-python.mjs`，仅用于验收并显式添加 `legacy/` 导入路径。不要在根目录恢复 `app/` 兼容包或符号链接。
- 旧项目检查从 `legacy/` 执行，如 `cd legacy && ../.venv/bin/python -m pytest tests tests_web -q`。旧应用如需连接数据库，必须显式指定正确DATABASE_URL；不能因路径变化创建空库。
- 不自动执行旧迁移/发布/MCP启动，不操作生产。Docker/CDN发布需要任务范围明确授权。

## 业务与数据约束

1. 结算域与作业域隔离：作业写入仅限 `bd_*`，结算四表不加列，不互相读写口径；迁移保持现有数据库结构、ID与历史快照。
2. 不读取真实 `raw_records` 隐私数据；旧 `legacy/app/services/bd_store.py` 的既有只读门店宇宙例外限原指定字段。`data/`、上传原件、凭证不入仓库。
3. 业务逻辑在服务层，页面不另造工资/找平口径。验收与同库SQL及旧实现对齐，不用历史快照数字当固定断言。
4. 找平必须区分未回收余额与本月应扣补；后者含本月已实扣＋仍未回收。两期应付沿用 `pay_plan`，实发台账不可覆盖，导出发薪表会登记发放。
5. 列表查询保持常数条，避免逐行权限SQL；账号按 `person_code` 判重，`users.role`（端权限）与团队角色分开。
6. 业务写操作保留CSRF、权限、封账、版本冲突及快照保护。未知值不能伪造为0，不因页面展示重新生成正式地图。
7. 管理端采用Appica默认主题；员工端Ant Design Mobile同步主题。导航使用真实a链接，成功反馈仅Toast；错误、风险和结果明细保留。
8. 用户/其他agent未提交改动必须保留。目录收敛保留原字节与新文件，不reset、不清理运行资产。
