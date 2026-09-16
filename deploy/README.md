# 部署手册（store.visitworld.me）

完整设计见 `docs/superpowers/specs/2026-09-04-store-visitworld-deployment-design.md`。
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
