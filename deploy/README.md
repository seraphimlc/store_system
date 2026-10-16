# 部署手册（store.visitworld.me）

部署要点见 `AGENTS.md` §发布流程。
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
