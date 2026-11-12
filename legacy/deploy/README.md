# 部署手册（store.visitworld.me）

## 当前范围覆盖：不迁移 MCP / OAuth（2026-11-11）

用户确认 MCP 未使用，OAuth 也已排除。新栈新增的 MCP 源码、测试、脚本、SDK 依赖和网关转发已撤回；相关公网路径明确返回 404。此前 MCP 实现记录、工具数量及待迁移清单仅为历史记录，不再作为待办或验收门槛。旧 Python `mcp_service/**`、历史表与数据保留，数据库连接配置不变。后续聚焦管理端、员工端、业务 API、地图/脚本迁移、本地 Docker/CDN 部署及 502 根因验证。

本次未部署或重启运行容器；当前已发布本地镜像须在后续本地发布时更新，生产未操作。


> React / NestJS 新系统的本地构建、Docker 与 OSS/CDN 发布规划见[重构与部署计划](../docs/计划-React-NestJS重构与部署.md)。本地流程已实现，使用方式见[新部署手册](../deploy-next/README.md)，真实CDN与生产发布尚未验收；下文继续适用于旧系统。

完整发布阶段、检查点及2026-11-09约41分钟发布复盘见仓库 [`README.md`](../README.md)「部署」；**命令和配置唯一权威仍是 `AGENTS.md` §发布流程**。纯代码发布与含迁移发布不是同一条耗时路径：迁移才需要生产备份、恢复库彩排、停写与数据不变量核对；每次都要测试、同时构建web/MCP并完成健康/冒烟验收。不要按41分钟预估每次发布，也不要为赶时间跳过迁移安全检查。
生产机：**`ssh store-prod`**（旧机 `ssh store-old` **只留数据供回滚，不参与发布**）。
**上线 / 回滚 / 恢复一律照 `AGENTS.md` §发布流程**（那是唯一权威，含 `.env` 正确放法、
必须同时 build `web`+`mcp`、别用 `| tail` 吃掉退出码等踩过的坑）。速查：

```bash
# 上线（第 3、4 步）
ssh store-prod "install -m600 /opt/store-settle/deploy.env /opt/store-settle/releases/$TS/deploy/.env"
ssh store-prod "cd /opt/store-settle/current/deploy && docker compose build web mcp && docker compose up -d --no-deps web mcp"
# 健康
curl -fsS http://127.0.0.1:3660/healthz
# 建管理员（首次）
docker compose -f current/deploy/compose.yaml exec web python -m app.cli create-admin
# 备份 cron（首次）
#   15 2 * * * /opt/store-settle/current/deploy/backup.sh >> /var/log/store-settle-backup.log 2>&1

# 回滚：current 指回上一个 release，再 up -d
ssh store-prod "ln -sfn /opt/store-settle/releases/<上一个TS> /opt/store-settle/current && cd /opt/store-settle/current/deploy && docker compose up -d"

# 恢复演练
gunzip -c backups/store_<ts>.sql.gz | docker compose -f current/deploy/compose.yaml exec -T db psql -U store_settle store_settle
tar xzf backups/uploads_<ts>.tar.gz -C /opt/store-settle/
```

## 端口 / 数据卷

## 说明

- 端口：web 只绑 `127.0.0.1:3660`；PG 只绑 `127.0.0.1:5433`（不与服务器现有 5432/PM2 服务冲突）。
- 升级：新 release → 切 current → compose up -d（旧镜像仍在可回滚）。
- 数据目录 volume：`store_pgdata`（PG）、`store_uploads`（上传原件）。

## 静态资源压缩与缓存（2026-10-06"方案 A"）

**问题**：线上 nginx **完全没开 gzip、也没给缓存头** → 每个页面裸传 ~120KB JS/CSS
（`htmx` 48KB + `alpine` 45KB + `app.css` 27KB；绩效/报表页再加 `chart.js` 205KB）。

**改法**（`deploy/nginx.store-settle.conf`，与生产 `/etc/nginx/conf.d/store.conf` 一致）：

1. **全站 gzip**：`gzip on` + `gzip_proxied any`（**关键**：本站全是反代响应，
   `gzip_proxied` 默认 `off` 时不会压缩）+ `gzip_vary on`（给 CDN/代理正确的 `Vary`）。
   `gzip_types` 里**故意不含** `text/event-stream` → `/mcp` 的 SSE 不被缓冲卡死；
   `/mcp` 里另加 `gzip off` 兜底。
2. **`/static/` 长缓存**：`add_header Cache-Control "public, max-age=31536000, immutable" always`。
   之所以敢给 1 年：模板里所有引用都带 `?v={{ static_ver(...) }}`（文件 **mtime+size**）
   → 文件一改版本号就变，**永远不用手工刷缓存/CDN**。
   ⚠️ `add_header` 会覆盖继承来的同名头；本机 `options-ssl-nginx.conf` 里没有 `add_header`，
   所以安全（若以后加了 HSTS 之类，要在这个 location 里重复写一遍）。

**生效方式**（改的是宿主机 nginx，不走容器发布）：
```bash
ssh store-prod "cp -a /etc/nginx/conf.d/store.conf /etc/nginx/conf.d/store.conf.bak.$(date +%Y%m%d_%H%M%S)"
scp deploy/nginx.store-settle.conf store-prod:/etc/nginx/conf.d/store.conf
ssh store-prod "nginx -t && nginx -s reload"     # 失败就把 .bak 拷回来再 reload
```

**实测效果**（2026-10-06 公网真 Chrome）：每页 JS/CSS **126,545 → 42,528 字节，省 66%**（htmx 3.0× / app.css 3.5× / alpine 2.8× / emp_select 2.5× / chart 2.9×）。

内容正确性：解压后的 `app.css` 与生产 release 里的文件 **md5 完全一致** ✓。

新版本地发布构建会将 `data/bd_zones/*.roads-v1.source.json`（可用 `NEXT_ZONE_DATA_DIRECTORY` 指定源目录）冻结进 API 镜像 `/app/zone-evidence`，并在发布 manifest 的 `zoneEvidence` 记录文件大小和 SHA256。它是网格合并的只读河道证据；坏文件或不完整快照会阻止构建，运行时无需挂载宿主目录。无证据时仅能合并共边连续网格，不能凭空批准跨河。

新管理端基础网格地图延用 `GOOGLE_PUBLIC_API_KEY`（浏览器公钥，应配置网站来源限制）。本地发布将该项传入运行容器，管理员通过配置接口按需加载 Google Maps JS；未配置时保留列表选择。地图回归默认使用SDK模拟，不自动调用真实供应商或启用Google Places。

MCP本地候选 `p5-mcp-auth-a` 已接入新API的 `/mcp`，当前工具目录只开放 `visit_whoami`。Access Token沿用现有 `api_tokens`，不启用旧开发静态/Bootstrap Token回退；`VISIT_OAUTH_RESOURCE` / `VISIT_OAUTH_ISSUER` 默认本地 `http://localhost:8080`。资源发现可用，OAuth授权/刷新和其余业务工具尚未迁移，不据此切换生产MCP客户端。协议验收可运行 `node --env-file=.env.next.local scripts/verify-p5-mcp.mjs`，只操作隔离夹具；Python客户端使用其既有虚拟环境，未进入新运行镜像。

2026-11-11范围调整：用户要求跳过OAuth，新源码移除未完成OAuth模块；MCP继续使用Access Token，OAuth地址明确404且资源元数据不广告授权服务器。应用数据库配置不改。其他agent即将将store_settle完整复制到store_settle_refactor_p1，目前本地候选容器全部停止，不应在复制期间启动。此前空隔离库测试包含全员口令重置等全库写操作，完整副本复制后须先另行隔离这些测试，不能直接运行release:build内全量数据库测试。

2026-11-11测试隔离已落实：`pnpm test` / `pnpm test:integration`（含release:build调用）使用现有本机PostgreSQL容器导出schema-only，在随机临时库运行，finally仅删除本次创建的临时库；应用与.env.next.local连接不变。运行证据在data/refactor/test-isolation-last.json。当前135项测试全部执行通过，源副本关键计数与口令摘要前后一致。不要绕过运行器手工把完整副本URL传给旧node --test；浏览器脚本还需专用API隔离，不能只替换夹具连接。
