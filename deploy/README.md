# 部署手册（store.visitworld.me）

部署要点见 `docs/索引.md` §部署。
服务器：8.216.43.224（root 经 `~/.ssh/default.pem`，Ubuntu 24.04，Docker+Compose 已装）。

## 上线步骤

1. DNS：把 `store.visitworld.me` A 记录指向 `8.216.43.224`（阿里云控制台）。
2. 上传发布：
   ```bash
   ssh -i ~/.ssh/default.pem root@8.216.43.224
   mkdir -p /opt/store-settle/releases
   # 本地：rsync 仓库（排除 .venv/.git/tests_*/data）到 /opt/store-settle/releases/<ts>
   ```
3. 配置：`cd /opt/store-settle/releases/<ts>/deploy && cp .env.example .env`，填 SECRET_KEY/DB_PASSWORD/ADMIN_PASSWORD，`chmod 600 .env`。
4. 构建启动（entrypoint 自动 alembic upgrade）：
   ```bash
   ln -sfn /opt/store-settle/releases/<ts> /opt/store-settle/current
   docker compose -f current/deploy/compose.yaml up -d --build
   curl -fsS http://127.0.0.1:3660/healthz
   ```
5. 建管理员：`docker compose -f current/deploy/compose.yaml exec web python -m app.cli create-admin`
6. Nginx + 证书：
   ```bash
   cp /opt/store-settle/current/deploy/nginx.store-settle.conf /etc/nginx/conf.d/
   nginx -t && systemctl reload nginx
   certbot --nginx -d store.visitworld.me   # 自动补 ssl 与 80→443
   curl -fsS https://store.visitworld.me/healthz
   ```
7. 备份 cron：`crontab -e` → `15 2 * * * /opt/store-settle/current/deploy/backup.sh >> /var/log/store-settle-backup.log 2>&1`（保持脚本路径固定到 /opt/store-settle/backup.sh 更稳）。

## 回滚

```bash
ln -sfn /opt/store-settle/releases/<上一个ts> /opt/store-settle/current
docker compose -f current/deploy/compose.yaml up -d
```

## 恢复演练

```bash
gunzip -c backups/store_<ts>.sql.gz | docker compose -f current/deploy/compose.yaml \
  exec -T db psql -U store_settle store_settle
tar xzf backups/uploads_<ts>.tar.gz -C /opt/store-settle/
```

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

**实测效果**（2026-10-06，公网真 Chrome 打开 `/tasks`）：
| 文件 | 压缩前 | 压缩后 | 倍数 |
|---|---|---|---|
| htmx.min.js | 48,101 | 15,843 | 3.0× |
| app.css | 27,080 | 7,793 | 3.5× |
| alpine.min.js | 44,659 | 16,223 | 2.8× |
| emp_select.js | 6,705 | 2,669 | 2.5× |
| chart.umd.min.js | 205,399 | 70,309 | 2.9× |
| **每页合计** | **126,545** | **42,528** | **省 66%** |

内容正确性：解压后的 `app.css` 与生产 release 里的文件 **md5 完全一致** ✓。
