# MCP 上线发布清单（含新表迁移与回填）

> 目标：把「MCP 接入 + 薪资/找平四表重构 + 每用户身份认证」一起发布到线上（store-prod）。
> 原则：**不重导数据**；建新表 + 用现有数据回填；已发薪月份口径不被改写。

## 0. 上线前（本地已完成的验证）

- 测试：`mcp_service` 323 passed；`tests`+`tests_web` 131 passed
- 数据：本地按线上顺序零人工干预导入 6 份文件，与线上逐人一致（仅 1 条为"按用户要求合并的同店两写法"）
- 认证：真实员工 token 端到端验收全过（越权拦截 / 状态联动 / 审计留痕）
- 自洽：`visit_verify_integrity` 8/8 通过

## 1. 备份（必做）

```bash
ssh store-prod "docker exec deploy-db-1 pg_dump -U store_settle -d store_settle \
  > /opt/store-settle/backups/pre_mcp_$(date +%Y%m%d_%H%M%S).sql"
```

## 2. 准备 `.env` 新增项

在 `deploy/.env` 追加（`MCP_BOOTSTRAP_TOKEN` 随便一个随机串，静态 token 已关，仅满足启动校验）：

```
MCP_BOOTSTRAP_TOKEN=<随机串>
```

## 3. 发布代码

```bash
TS=$(date +%Y%m%d_%H%M%S)
ssh store-prod "mkdir -p /opt/store-settle/releases/$TS"
rsync -a --delete -e ssh --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude '*.pyc' --exclude '*.db' --exclude 'data/' --exclude '.pytest_cache' \
  --exclude '.DS_Store' --exclude 'scripts/2026-09_*' ./ store-prod:/opt/store-settle/releases/$TS/
ssh store-prod "cp /opt/store-settle/current/deploy/.env /opt/store-settle/releases/$TS/deploy/.env; \
  rm -f /opt/store-settle/current; ln -s /opt/store-settle/releases/$TS /opt/store-settle/current"
# web（entrypoint 自动 alembic upgrade → 建新表/加列，**不动既有数据**）
ssh store-prod "cd /opt/store-settle/current/deploy && docker compose build web mcp && docker compose up -d --no-deps web"
```

## 4. 回填新表数据（**只写新表**）

```bash
# 先 dry-run 看数字
ssh store-prod "cd /opt/store-settle/current/deploy && docker compose exec web \
  python scripts/backfill_prod_new_tables.py --dry-run"
# 实跑（幂等：先清后建）
ssh store-prod "cd /opt/store-settle/current/deploy && docker compose exec web \
  python scripts/backfill_prod_new_tables.py"
```

回填内容（口径：**8 月两期已发完、9 月上半月已发、7 月不管**）：
- `payroll_adjusts` ← `payroll_period_rows.diff_amount` 逐人
- `payroll_payments` ← 已发期的分期表金额（零额期不登记）
- `payroll_settlement_links` + 找平进度 ← FIFO 分配

## 5. 启动 MCP 服务

```bash
ssh store-prod "cd /opt/store-settle/current/deploy && docker compose up -d mcp"
ssh store-prod "docker compose -f /opt/store-settle/current/deploy/compose.yaml logs --tail=30 mcp"
```

Nginx 追加 `/mcp` location（配置已在 `deploy/nginx.store-settle.conf`）：

```bash
ssh store-prod "cp /opt/store-settle/current/deploy/nginx.store-settle.conf /etc/nginx/sites-available/ 2>/dev/null; nginx -t && systemctl reload nginx"
```

## 6. 验收（逐项必须通过）

```bash
# 6.1 老表未被改动（逐人对比迁移前后：点数/工资/差异/结转必须一致）
ssh store-prod "docker exec deploy-db-1 psql -U store_settle -d store_settle -At -F'|' -c \
  \"SELECT month, person_code, points, salary FROM month_perf_records ORDER BY 1,2;\""

# 6.2 新表自洽（8 项）
ssh store-prod "cd /opt/store-settle/current/deploy && docker compose exec web \
  python scripts/verify_payroll_logic.py"

# 6.3 MCP 可达 + 工具清单（经公网域名，验证 Host 校验与反代）
curl -s -X POST https://store.visitworld.me/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer <线上签发的 token>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' | head -c 200

# 6.4 身份认证（员工 token 越权必须被拦）
#   在线上给某员工签发 token（/my/token）→ 用该 token 调 visit_month_salary → 应 FORBIDDEN_TOOL
```

## 7. WorkBuddy 侧

1. 连接器 URL 改为 **`https://store.visitworld.me/mcp`**
2. 每个用户**用自己的 token**（登录网页 →「我的 Token」生成 → 粘贴到连接器 headers）
3. 员工账号只暴露"我的"工具（服务端已强制；越权调用返回 FORBIDDEN_TOOL）

## 8. 回滚

- 代码：`ln -sfn /opt/store-settle/releases/<上一个> /opt/store-settle/current` + `docker compose up -d`
- 数据：新表可 `alembic downgrade` 删除；老表零改动，无需恢复
- MCP 服务：`docker compose stop mcp`（不影响 web 业务）

## 9. 上线后注意事项

- **导出发薪表 = 登记发放事实**（台账写入 + FIFO 冲找平）→ 预览请用 `dry_run=true`（如后续补上）
- **已发薪月份不再被重算改写**（店铺合并仍会做，但已发月的正式表保持原样）
- 同店两种写法（空格/全角）上传时**自动合并**（`STORE_AUTO_MERGE_EXACT` 默认开，可 `=0` 关）
