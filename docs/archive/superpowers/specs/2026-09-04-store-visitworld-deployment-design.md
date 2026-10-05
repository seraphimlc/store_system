# 巡店结算系统部署设计（store.visitworld.me）

- 日期：2026-09-04
- 版本：草案 v0.1（待 Web v1 实现完成后执行；执行前需 DNS 就绪确认）
- 参考：既有 `thai-visitworld` 部署流程（8.216.43.224、Nginx+Certbot、releases 布局可回滚）
- 前置：巡店结算系统 **Web/DB v1**（FastAPI + PostgreSQL 16 + Alembic）实现完成（本仓库后续里程碑）

---

## 1. 目标与约束

把巡店结算系统以 **store.visitworld.me** 发布到 8.216.43.224（阿里云 ECS，root 经 `~/.ssh/default.pem`），与服务器上现有服务（PM2×4、thai-travel、unified-feature-quote-system、zhixu-learning、sinan 与各自 DB）**完全隔离、零影响**。

| 约束 | 处理 |
|---|---|
| 现有 80/443 与多个应用端口占用 | 新应用只监听 **127.0.0.1**，经系统 Nginx 反代；不加公网端口 |
| 5432 已有他人的 PostgreSQL | 本系统用**独立 Postgres 16 容器**，宿主映射到 **127.0.0.1:5433**（不绑 0.0.0.0）|
| 磁盘剩 9.6G / 内存可用 4.3G | 资源克制：gunicorn 2 workers、PG 小配置、备份保留 30 天 |
| 不能破坏现有 Nginx 站点 | 新增独立 `store-settle.conf`（conf.d），不动既有 site |
| 回滚能力 | 仿 thai-travel：releases/current/manifests 布局 + compose 双版本共存切换 |

---

## 2. 部署架构

```
DNS: store.visitworld.me  A → 8.216.43.224（用户在阿里云解析控制台添加）
                    │ https:443
                    ▼
[system Nginx]  server store.visitworld.me → 127.0.0.1:3660
                    │ (proxy_pass, ws 无需)
                    ▼
[FastAPI(gunicorn+uvicorn) 容器, 仅容器网络]
    ├─ web     :3660  （容器内 uvicorn workers=2，非 root 用户运行）
    ├─ db      :5433   postgres:16-alpine, volume pgdata, 仅 127.0.0.1
    └─ (可选) backup 宿主 cron: pg_dump + uploads tar
数据目录 /opt/store-settle/{pgdata, uploads, backups}
```

- 网络：compose 项目名 `store-settle`；容器不暴露公网端口；web 的 3660 以 `ports: ["127.0.0.1:3660:8000"]` 只绑本机。
- 域名与证书：首次部署时 certbot `--nginx -d store.visitworld.me` 签发；HTTP→HTTPS 由 certbot 自动插入的跳转保证。
- 健康检查：`GET /healthz`（web 内实现，检查 DB 可达）→ nginx `proxy_next_upstream` 与运维脚本使用。

## 3. 目录与发布布局（可回滚）

```
/opt/store-settle/
├── releases/<YYYYmmddHHMMSS>/      # 不可变发布：仓库代码 + Dockerfile + compose.yaml + alembic 迁移
├── current -> releases/<ts>        # 活动版本符号链接
├── manifests/<ts>.json             # 发布元数据（git 短哈希、镜像 tag、时间、迁移版本）
├── pgdata/                         # PG 卷（宿主 bind mount）
├── uploads/                        # 上传原件存档卷
├── backups/                        # pg_dump + uploads tar（宿主 cron）
└── .env                            # SECRET_KEY/DB_PASSWORD 等（600，仅 root）
```

发布步骤（上线操作手册，Web v1 交付后执行）：
1. 打包：`git archive` 或 rsync 仓库 → `releases/<ts>`（排除 .venv/.git/真实数据）
2. `cd current` 后 `docker compose build`（或拉取预构建镜像 tag）
3. 迁移：`docker compose run --rm web alembic upgrade head`（只允许在 release 内执行）
4. `ln -sfn releases/<ts> current`；`docker compose up -d`；`curl -fsS https://store.visitworld.me/healthz`
5. 写 `manifests/<ts>.json`；失败→ `current` 回指上一版本 + `docker compose up -d`（旧镜像仍在）即回滚

## 4. compose 服务要点（骨架，Web 计划中落地为 `deploy/` 目录）

```yaml
# deploy/compose.yaml（示意）
services:
  web:
    build: .
    env_file: .env
    ports: ["127.0.0.1:3660:8000"]
    volumes: ["./uploads:/data/uploads"]
    depends_on: [db]
    restart: unless-stopped
  db:
    image: postgres:16-alpine
    environment: { POSTGRES_DB: store_settle, POSTGRES_PASSWORD: "${DB_PASSWORD}" }
    ports: ["127.0.0.1:5433:5432"]
    volumes: ["/opt/store-settle/pgdata:/var/lib/postgresql/data"]
    restart: unless-stopped
```

- `.env` 生成：`openssl rand -hex 32` 作 SECRET_KEY；DB_PASSWORD 随机；只存宿主（600）。
- 镜像非 root：Dockerfile 建普通用户运行 uvicorn；上传目录挂只读或受控子目录。
- Alembic：迁移文件随代码入库；`alembic upgrade head` 在 compose run 中执行（幂等）。

## 5. Nginx（系统级，独立 conf）

```nginx
# /etc/nginx/conf.d/store-settle.conf
server {
    listen 443 ssl;               # 证书由 certbot 自动管理
    server_name store.visitworld.me;
    location / {
        proxy_pass http://127.0.0.1:3660;
        proxy_set_header Host $host; X-Real-IP; X-Forwarded-For; X-Forwarded-Proto https;
        client_max_body_size 60m;   # 上传上限 50MB + 余量
    }
}
# certbot --nginx -d store.visitworld.me 自动补 ssl 块与 80→443 跳转
```

- 不与其他 conf 冲突；`nginx -t` 通过后 `systemctl reload nginx`。

## 6. 备份与恢复（宿主 cron）

```cron
15 2 * * * /opt/store-settle/scripts/backup.sh >> /var/log/store-settle-backup.log 2>&1
```

`backup.sh`：
1. `docker compose -f /opt/store-settle/current/compose.yaml exec -T db pg_dump -U store_settle store_settle | gzip > backups/store_<date>.sql.gz`
2. `tar czf backups/uploads_<date>.tar.gz -C /opt/store-settle uploads`
3. 清理 >30 天；校验 gzip 可读；留最近 7 天清单
恢复演练：新容器/新库上 `gunzip | psql` 导入 → 页面抽查计数与 run 历史一致。

## 7. 安全与运维清单

- 防火墙（阿里云安全组 + 宿主 ufw，如启用）：只开 22/80/443；5433 仅本机（Nginx/应用同机访问）。
- 应用仅服务端渲染 + session cookie（HttpOnly/SameSite）+ CSRF；上传白名单 .xlsx + 大小限制；.env 不进镜像层（env_file 注入）。
- 日志：`docker compose logs -f web`；宿主轮转 `/var/log/nginx/`（默认 done）。
- 升级：新 release → 迁移 → 切 current → healthz → 保留旧 release 便于回滚；规则版本与 DB 迁移版本记入 manifests。
- 更新提醒：服务器磁盘 75% 已用；上线前清理旧 release 与 thai 旧产物（先征求现有站点所有者意见），并确保 ≥6G 余量。

## 8. 执行前置（上线 checklist）

- [ ] Web/DB v1 实现完成、单元/E2E 绿、真实文件验收通过
- [ ] 用户确认：`store.visitworld.me` 已在阿里云 DNS 添加 A 记录 → 8.216.43.224（TTL 生效）
- [ ] 服务器：确认磁盘清理方案、5433 空闲、nginx conf.d 可写
- [ ] 本机：default.pem 可用（已验证）
- [ ] 首次部署 + certbot 证书 + https 检查 + 备份 cron 安装 + 恢复演练
- [ ] 交付：部署手册 `deploy/README.md`（含回滚与备份恢复步骤）随 Web 代码提交

---

## 附录：与 thai-visitworld 部署的差异

| | thai-visitworld | 巡店系统 |
|---|---|---|
| 运行形态 | PM2 node 进程（3500） | Docker Compose（web+db 两容器）|
| 数据库 | 无 | Postgres 16 独立容器（5433）|
| 发布载体 | /opt/thai-travel/releases | /opt/store-settle/releases（compose 版）|
| 回滚 | pm2 切换 release 目录 | 切 current 符号链接 + 旧镜像启动 |
| 新增 | — | Alembic 迁移、pg_dump 备份、uploads 存档 |
