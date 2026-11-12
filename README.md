# 巡店结算与作业系统

当前系统采用 React 19 前端与 NestJS API/Worker，管理端和员工H5独立构建，保留既有数据库和业务口径。

| 目录 | 内容 |
|---|---|
| `apps/admin` | 管理端：React、Tailwind CSS、Appica |
| `apps/staff` | 员工H5：React、Ant Design Mobile |
| `apps/server` / `apps/worker` | NestJS API与异步任务 |
| `packages` | 共享契约、业务逻辑与客户端 |
| `scripts` | 新系统开发、隔离验收、发布及Python对照适配器 |
| `deploy-next` | 当前Docker/CDN发布 |
| `legacy` | 完整旧Python项目、迁移、测试、MCP与旧部署脚本 |
| `docs` | 共享规格、记录与交接 |

开发运行 `pnpm dev`，管理端8001、员工端8002、API3000。本地Docker入口8000；数据库连接与发布步骤见 [deploy-next/README.md](deploy-next/README.md)。

开始修改前读 [AGENTS.md](AGENTS.md) 与 [当前交接](docs/交接-当前状态.md)。旧项目从 `legacy/` 查阅，参考 [旧项目说明](legacy/README.md) 和 [旧操作手册](legacy/AGENTS.md)。

数据库、`data/`、现有环境文件、`.venv/`、`.release/` 保持根目录原位；目录归档不会执行迁移或更新正在运行的Docker。
