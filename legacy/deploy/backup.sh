#!/usr/bin/env bash
# 备份：pg_dump + uploads tar；保留 30 天。宿主 cron: 15 2 * * * ...
# 注意：release 布局为 <release>/deploy/compose.yaml，用 --project-directory 定位。
set -euo pipefail
BASE=/opt/store-settle
TS=$(date +%Y%m%d_%H%M)
mkdir -p "$BASE/backups"
docker compose --project-directory "$BASE/current/deploy" \
  -f "$BASE/current/deploy/compose.yaml" exec -T db \
  pg_dump -U store_settle store_settle | gzip > "$BASE/backups/store_$TS.sql.gz"
tar czf "$BASE/backups/uploads_$TS.tar.gz" -C "$BASE" uploads 2>/dev/null || true
# 校验 gzip 可读
gzip -t "$BASE/backups/store_$TS.sql.gz"
find "$BASE/backups" -name "*.gz" -mtime +30 -delete
echo "backup ok $TS"
