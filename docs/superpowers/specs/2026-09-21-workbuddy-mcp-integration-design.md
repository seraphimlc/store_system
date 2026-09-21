# 巡店结算系统 × WorkBuddy 接入设计（WorkBuddy MCP Integration Design）

- 日期：2026-09-21
- 状态：定稿草案（待用户审阅）
- 配套：`docs/索引.md`、`docs/结算主流程设计-v3.md`、`AGENTS.md`
- 核心思路（用户提出）：**把巡店结算系统变成 WorkBuddy 能调用的能力**——在 WorkBuddy 的工作台里用自然语言查询和操作本系统，而不是每次打开浏览器登录网页。

---

## 1. 目标与原则

**目标**：WorkBuddy（腾讯全场景 AI 办公工作台）通过 MCP 协议调用本系统的业务能力，覆盖三类场景：

| 场景 | 说明 |
|---|---|
| 只读查询与问答 | "9 月正式表多少人/总点数""某人这个月绩效多少""对账差异有哪些店" |
| 管理端全流程执行 | 口述任务让 Agent 跑：判定 → 出正式表 → 申诉处理 → 绩效 → 对账 → 找平（上传除外，见 §2） |
| 定时自动化与推送 | 每月出表后自动汇总推送、对账差异告警（调度在 WorkBuddy 侧，本系统零调度代码） |

**原则**：

1. **能力层唯一真相**：业务能力只在 `app/api/` 实现一次，MCP 只是协议适配器。未来接企微助理、定时任务复用同一批函数，不重复实现业务逻辑。
2. **服务端说了算**：权限、封账、只读/写边界全部在服务端强制。**不依赖提示词约束 Agent 行为**——WorkBuddy 的执行环境不在本系统信任边界内。
3. **薄封装**：服务层（`app/services/*`）已经是 `{"ok": bool, "msg"/"added": ...}` 的 dict 契约（如 `flow.finalize_import` 注释明确"幂等：先删后插，可安全重复执行"），能力层几乎不做转换。
4. **可追溯**：每次工具调用落库审计，谁在什么时候让 AI 干了什么，永远可查。
5. **YAGNI**：不做员工自助入口（用户明确不要）、不做上传通道（见 §2）、不做调度器（WorkBuddy 侧已有）。

## 2. 已确认的需求决策

| 决策项 | 结论 | 说明 |
|---|---|---|
| 目标场景 | 只读问答 + 全流程执行 + 定时推送 | 不做员工自助（`/my/perf`、`/my/appeal` 不接入） |
| 运行形态 | **远端 MCP 挂在 `store-prod`**，与现有 FastAPI 同进程 | 不新增容器/端口；桌面、移动端、云端沙箱都能用 |
| 身份终态 | **每人各自绑定** | 委托授权粒度到人，可审计到人 |
| 鉴权分期 | **P1 用 Per-user Token，OAuth 后置到 P3** | 接口形状从第一版就按终态设计，届时业务代码不改 |
| 上传口径 | **上传留在网页，MCP 管传完之后的全流程** | 远端服务拿不到本地 Excel；且上传本来就该人工确认文件归属 |
| 写操作范围 | **四项全要**：出表、申诉判定、月度重算、改单价 | 因此安全设计加厚：封账闸门 + 强制预演 + 审计 |

## 3. 架构与分层

```
WorkBuddy（桌面 / 移动端 / 云端沙箱）
  │   MCP streamable-http    POST /mcp    Authorization: Bearer <每人一枚的 Token>
  ▼
app/mcp/        适配层：工具声明(schema + 中文描述) · 鉴权 · 审计 · 闸门
  ▼
app/api/        能力层：纯函数 (actor, **params) -> {"ok": bool, ...}    ← 唯一真相
  ▼
app/services/*  既有业务逻辑（语义零改动，仅 rebuild_month 加 dry_run）
  ▼
DB（本地 SQLite / 线上 PG）
```

**各层职责与接口**

| 层 | 做什么 | 怎么用 | 依赖 |
|---|---|---|---|
| `app/api/` | 一个业务能力一个函数：入参校验、权限、闸门、调服务层、归一化返回 | `from app.api import perf as api_perf; api_perf.month_summary(db, actor, month="2026-08")` | `app/services/*`、`app/models.py`、`app/api/guards.py` |
| `app/mcp/` | 声明工具（名称/描述/参数 schema）、把 MCP 调用转成 `app/api/` 调用、写审计 | 由 WorkBuddy 调用，或本地测试用 MCP client SDK | `app/api/`、`app/api/auth.py` |
| `app/api/guards.py` | 权限判定、封账判定、月份格式校验、`confirm_text` 校验 | 被 `app/api/` 各函数调用 | `app/config.py`、`app/models.py` |
| `app/services/*` | 既有业务逻辑 | 不变 | — |

**为什么同进程**：复用现有 DB Session、配置、Alembic 迁移、鉴权、部署流程（`docker compose build web`）；无需新增容器、端口、两跳鉴权。风险（MCP 故障影响 Web）用异常隔离 + 按 token 限流缓解（§7）。

## 4. 能力层（`app/api/`）

**统一约定**：

- 第一个参数固定为 `actor`（`Actor` 数据类：`uid`、`role`、`person_code`、`scopes`、`token_id`）；调用者身份由鉴权层解析后注入，**业务函数不自己解析 Token**。
- 第二个参数固定为 `db`（SQLAlchemy Session），沿用服务层签名习惯。
- 返回统一为 `{"ok": True, "data": {...}}` 或 `{"ok": False, "error": {"code","message","hint"}}`（§8）。
- 只读函数不得调用 `db.commit()`；写函数内部提交。

**只读能力（8 个）**

| 函数 | 入参 | 复用 |
|---|---|---|
| `perf.month_summary(db, actor, month)` | `month: YYYY-MM` | `services/perf.month_perf` + `FormalRecord` |
| `perf.person_perf(db, actor, month, keyword, limit=20)` | 姓名或工号模糊 | `services/perf.month_perf`、`salary_for`、`bonus_params` |
| `perf.ranking(db, actor, month, by="points"\|"salary", limit=20)` | — | `services/perf.month_perf` |
| `recon.status(db, actor, month)` | — | `services/recon`、`ReconTask`、`ReconResult` |
| `recon.diff(db, actor, month, only_issues=True, limit=200)` | — | `services/recon`、`PersonDailyStat`、`ReconDataRow` |
| `appeal.list_(db, actor, month, status="pending")` | — | `services/flow.appeal_list`、`AppealRecord` |
| `files.list_(db, actor, month)` | — | `ImportFile`、`RawRecord` |
| `payroll.rows(db, actor, month)` | — | `services/period.period_rows`、`carry_map` |

**写能力（4 个）**

| 函数 | 复用 | 闸门 |
|---|---|---|
| `files.finalize(db, actor, fid, confirm_text)` | `services/flow.finalize_import` | admin + 写 scope + 该文件所属月未封账 + 无 pending 申诉（服务层已判，透传 msg） |
| `rebuild.month(db, actor, month, dry_run, confirm_text)` | `services/flow.rebuild_month`（新增 `dry_run`） | admin + 写 scope + 未封账；`dry_run=True` 时不需要 `confirm_text` |
| `appeal.resolve(db, actor, appeal_id, decision, confirm_text)` | `services/flow.resolve_appeal` | admin + 写 scope + 未封账 |
| `perf.set_per_point(db, actor, month, value, confirm_text)` | `services/perf.set_month_per_point` | admin + 写 scope + 未封账 |

**服务层唯一改动**：`flow.rebuild_month(db, month, actor_id=None, dry_run=False)`。`dry_run=True` 时执行完重算与计数后 `db.rollback()`，返回 `{"ok": True, "dry_run": True, "will_delete": N, "will_insert": M, "affected_persons": K}`，不落库。改动限于函数尾部提交逻辑，不影响现有调用方（默认 `dry_run=False`）。

## 5. MCP 适配层与工具清单（`app/mcp/`）

**技术选型**：官方 `mcp` Python SDK（新增依赖，锁定版本），StreamableHTTP 传输，挂载路径 `/mcp`。

**工具命名**：统一前缀 `visit_`，避免与用户机器上其他连接器工具撞名（本机已有 263 个官方连接器）。

**三档权限**

| 档 | 工具 | 默认状态 |
|---|---|---|
| A · 只读 | `visit_month_summary`、`visit_person_perf`、`visit_perf_ranking`、`visit_recon_status`、`visit_recon_diff`、`visit_appeal_list`、`visit_file_list`、`visit_payroll` | **全开** |
| B · 写 | `visit_finalize_file`、`visit_rebuild_month`、`visit_resolve_appeal`、`visit_set_per_point` | **交付时用 `disabledTools` 关闭**，需要时人工在 WorkBuddy 里打开 |
| C · 永不给 | `delete_file`、`rerun`、`reset-all`、密码重置、直接写 `AdjustRecord` | **硬编码不进工具清单** |

**工具描述要求**：每个工具的 `description` 用中文写清"做什么 + 什么时候用 + 关键约束"，参数用 JSON Schema 声明 `enum`、必填、格式（月份 `pattern: ^[0-9]{4}-(0[1-9]|1[0-2])$`）。描述质量直接决定 Agent 选对工具的概率。

**`visit_rebuild_month` 强制预演**：工具描述明确要求"必须先 `dry_run=true` 看预测，向用户复述将删除/写入的条数并得到确认后，才能 `dry_run=false`"。服务端在执行 `dry_run=false` 时要求 `confirm_text` 与预期字面一致（§7）。

## 6. 鉴权设计

**P1 · Per-user Token**

新表 `api_tokens`：

| 列 | 说明 |
|---|---|
| `id` | PK |
| `user_id` | FK → `users.id`，绑定到具体的人 |
| `name` | 用途备注（如"刘畅的 WorkBuddy 桌面端"） |
| `token_hash` | 只存哈希（argon2，复用 `app/auth.py` 的 `hash_password`） |
| `scopes` | `read` / `read,write` |
| `created_at` / `last_used_at` / `revoked_at` | 签发、最近使用、吊销时间 |

- 新增页面 `/my/token`：本人生成（**明文只显示一次**）、查看列表、吊销；管理员可代他人签发。入口挂在 `/my/password` 附近。
- 鉴权流程：`Authorization: Bearer <token>` → 查 `api_tokens` → 校验哈希且未吊销 → 更新 `last_used_at` → 构造 `Actor`（含 `users.role`）→ 注入能力层。
- 失败一律 `401`，响应体带 `WWW-Authenticate: Bearer`。
- Token 存储：WorkBuddy 侧仅存本机（官方 `token-schema.json` 描述明确"仅存储在本机…不会上传到云端"）。

**P3 · MCP OAuth 2.1（终态）**

- 端点：`/.well-known/oauth-protected-resource`、`/.well-known/oauth-authorization-server`、`/oauth/register`（DCR）、`/oauth/authorize`（**复用现有登录页做授权确认**）、`/oauth/token`
- WorkBuddy 侧表现：连接器显示「需要认证」→ 点「连接」→ 浏览器登录巡店系统 → 确认授权 → 完成
- **兼容形状**：能力层与工具层完全不感知鉴权方式；只替换 `app/api/auth.py` 里"解析凭据 → 构造 Actor"的实现。`Actor` 里的 `uid/role/scopes` 两种方式都能填满。

**员工边界**：`main.py` 的 `staff_isolation` 中间件与各路由的 `role != "admin"` 检查是现有权限边界，**能力层必须等价复刻**：`Actor.role == "staff"` 时只允许访问与其 `person_code` 相关的只读数据，其余一律 `FORBIDDEN`。不得因为"MCP 是内部接口"而放松。

## 7. 闸门与安全

| 闸门 | 实现 | 拦截的效果 |
|---|---|---|
| 1 · 网络 | `/mcp` 仅接受 Bearer；无效/缺失 → 401；按 `token_id` 限流（如 60 次/分钟）；线上走 HTTPS | 未授权访问、AI 循环调用打爆服务 |
| 2 · 身份 | Token → uid → role → `Actor`；员工边界复刻（§6） | 员工越权读他人/管理数据 |
| 3 · 工具 | `scopes` 不含 `write` 时写工具直接 `FORBIDDEN_TOOL`；写工具在连接器配置层用 `disabledTools` 默认关闭 | 只读 Token 误写、AI 顺手调用 |
| 4 · 业务 | **封账闸门**（`SEALED_MONTHS` 配置，命中即拒绝所有写操作）+ 月份格式严格校验 + `confirm_text` 字面校验 | 改历史月、全角月份导致整月表被清空、误触 |
| 5 · 审计 | 每次调用写 `mcp_audit_log`（§9） | 不可追溯 |

**封账闸门（本设计唯一新增的业务概念）**：代码中原本没有"封账锁"（"8 月封账"只是流程约定，讲的是规则不回溯）。MCP 让 AI 能重算整月，因此必须把它变成代码：

- 配置项 `SEALED_MONTHS`（如 `2026-08`，逗号分隔，支持 `2026-08` 整月粒度）
- `app/api/guards.py` 提供 `assert_not_sealed(month)`，所有写能力入口调用
- 命中返回 `MONTH_SEALED`，`hint` 说明如何解除

**`confirm_text` 的诚实定位**：MCP 协议没有"弹窗等人工确认"的标准能力，Agent 完全可以自己填对这句话，**所以它不是安全边界**。它的价值是降低误触概率（缺这句话直接失败）。真正的边界是：role + scope + 封账 + `disabledTools`。

**月份格式校验**：复用 `flow.rebuild_month` 里那道"唯一关口"的严格写法 `re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month)`——注释已说明用 `\d` 会放行全角「２０２６-08」导致"先删后填 0 行、整月正式表被清空"。能力层用同一正则，且年份限 `1..9998`。

## 8. 返回契约与错误码（写给 Agent 看）

```json
{ "ok": true, "data": { ... } }
{ "ok": false, "error": { "code": "...", "message": "...", "hint": "..." } }
```

`hint` 的职责是**纠正 Agent 的下一步动作**，否则它会反复瞎试：

| code | 触发 | hint 示例 |
|---|---|---|
| `UNAUTHORIZED` | 无/坏 Token | "请在 WorkBuddy 连接器设置中重新填写 Access Token（登录巡店系统 → 个人设置 → 接入码）" |
| `FORBIDDEN_TOOL` | scope 不足或角色不够 | "该操作需要写权限的 Token，当前 Token 为只读" |
| `MONTH_SEALED` | 命中封账月 | "2026-08 已封账不可写入；如需修正请走对账找平流程" |
| `PENDING_APPEALS` | 出表/重算时有未决申诉 | "先调用 visit_appeal_list(month='2026-09') 处理申诉" |
| `BAD_MONTH` | 月份格式非法 | "月份必须是 YYYY-MM，例如 2026-08" |
| `CONFIRM_REQUIRED` | 缺/错 `confirm_text` | "请复述确认语：确认出表 2026-08" |
| `NOT_FOUND` | 文件/申诉 id 不存在 | "先调用 visit_file_list(month='2026-09') 获取正确 id" |

## 9. 审计

新表 `mcp_audit_log`：`id`、`token_id`、`user_id`、`tool`、`params_json`（脱敏：不含 Token 明文）、`ok`、`error_code`、`client_info`（MCP client 上报的名称/版本）、`duration_ms`、`created_at`。

- 每次工具调用落一行（成功与失败都落）
- 管理端新增只读页 `/mcp-audit`（管理员可见）：按人/工具/时间筛选
- 与既有 `audit-log` 概念不冲突：这是接入层审计，不替代业务审计

## 10. 集成陷阱（实现时必须处理）

1. **lifespan 必须接上**：`create_app()` 目前没有 `lifespan`（`app/main.py:7-9`）。MCP 的 StreamableHTTP 会话管理器需要挂进 ASGI lifespan，否则**静默连不通**。
2. **中间件必须放行 `/mcp`**：`staff_isolation` 中间件对带 `ss` cookie 的 staff 会把非白名单路径 302 到 `/my/perf`（`app/main.py:27-66`）。若请求同时带 cookie，`/mcp` 会被重定向成 HTML，客户端解析失败。实现时让 `/mcp` 完全绕过该中间件（而非仅加进 `STAFF_ALLOWED`），因为 MCP 请求本就不该走基于 cookie 的页面隔离。
3. **CSRF 与 `require_login` 不可复用**：路由层的 `csrf_ok(request, csrf_token)` 与 `Depends(require_login)` 是给 HTML 表单用的（`app/routers/settle_r.py:36-41` 可作范例）。MCP 侧鉴权必须在能力层重做，不得假装复用了 CSRF。
4. **月份格式必须过严格关口**（§7），不能让 Agent 的自由文本直达 `rebuild_month`。
5. **`/v3/*` 是历史别名**，MCP 不暴露 v3 路径，只走能力层。
6. **线上演示保护**：演示期只签发 `read` scope 的 Token，写工具在 `disabledTools` 保持关闭；这条同时写进 `AGENTS.md` 的"已知坑"。

## 11. 连接器包交付物（`deploy/connector/`）

WorkBuddy 的连接器就是一个目录（本机 263 个官方连接器均为此结构：`mcp.json` + 可选 `token-schema.json` + 可选 `skills/<name>/SKILL.md`）。交付同结构，让"接入"变成"复制一段 JSON"：

| 文件 | 内容 |
|---|---|
| `mcp.json` | `{"mcpServers":{"visit-settle":{"type":"streamableHttp","url":"https://store.visitworld.me/mcp","headers":{"Authorization":"Bearer ${VISIT_SETTLE_TOKEN}"},"timeout":60000,"disabledTools":[...写工具...]}}}` |
| `token-schema.json` | `title`/`description`/`fields[{key:"VISIT_SETTLE_TOKEN", label, type:"password", required, placeholder:"登录巡店系统 → 个人设置 → 接入码", description}]`——把用户引到正确位置 |
| `skills/巡店结算/SKILL.md` | 业务口径指令包：1点/2点与点数口径、每点单价与奖金规则（每满门槛点奖、门槛按月可配）、找平金额正负含义、月份格式、**先查后写**、写操作须先复述再执行 |

未来若上架官方连接器市场，这个包可直接复用（官方索引 `connectors.json` 定义了 `auth_injection_rules`，其中 `token_type: "mcp-oauth"` 的 `value_template: "Bearer ${access_token}"` 与我们的形状一致）。

## 12. 测试策略

| 层 | 方式 | 关键断言 |
|---|---|---|
| 能力层 | pytest 直调函数（沿用 `tests_web/` fixture 风格）+ 内存/SQLite 库 | 权限、封账、月份格式、返回契约 |
| MCP 层 | 官方 MCP client SDK 端到端连同进程 `/mcp` | 工具清单与描述完整；无 Token → 401；staff Token 调写工具 → `FORBIDDEN_TOOL`；写工具在 `disabledTools` 下不可见 |
| **契约基准** | 断言 `visit_month_summary("2026-08")` 返回正式表 **12511**、总点数 **16791**、1点/2点 **8231/4280** | 用 `AGENTS.md` 的验收基准当回归契约——任何改坏口径的重构会被立刻拦住 |
| 预演 | `visit_rebuild_month(dry_run=true)` 后断言 `formal_records` 行数不变 | 预演绝不落库 |
| 审计 | 调用后查 `mcp_audit_log` | 成功/失败各落一行；`params_json` 不含 Token 明文 |
| 闸门 | 封账月写操作、pending 申诉时出表 | 返回对应 code 与可执行的 hint |

## 13. 分期与验收标准

| 期 | 内容 | 验收标准 |
|---|---|---|
| **P0** | 连通性 spike：最小 MCP（`ping` + 读 demo 库的 `system_summary`）+ 错误 Token 负例，在本机 WorkBuddy 真实连一次 | ① WorkBuddy 能连上并列出工具 ② 正确 Token 可调用 ③ 错误 Token 被拒且行为可见 ④ 记录握手/认证/报错的真实表现 |
| **P1** | 能力层 + 8 个只读工具 + `/my/token` + 审计 + 连接器包 + 文档 | WorkBuddy 里问"8 月正式表多少人、总点数多少"得到 12511/16791；审计页可见调用记录 |
| **P2** | 4 个写工具 + 封账闸门 + `dry_run` 预演 + `confirm_text`、`flow.rebuild_month` 加 `dry_run` | 只读 Token 调写工具被拒；封账月被拒；重算预演不落库、真跑结果与网页操作一致 |
| **P3** | OAuth 2.1 替换 Token | WorkBuddy 里点「连接」→ 浏览器完成授权即可使用，无需手工贴 Token |
| **P4** | WorkBuddy 侧定时任务 + 企微机器人推送 | 月度汇总/差异告警自动送达，本系统无调度代码 |

**每期完成后**：更新 `docs/索引.md` 与 `AGENTS.md`（仓库强制要求）；发布走既有 rsync + `docker compose build web` 流程，线上无新增端口。

## 14. 未验证假设与验证方式

诚实列出本设计中**尚未被事实验证**的部分，以及怎么验：

| 假设 | 风险 | 验证方式 |
|---|---|---|
| WorkBuddy 桌面端能连本机 `127.0.0.1` 的 MCP 端点 | 若客户端把 MCP 流量走云端网关，本地 spike 无法成立，P0 需改为公网临时端点 | **P0 spike**（先本地，不通则临时公网映射） |
| 远端 MCP 的路径挂载与握手细节（`/mcp` 是否需尾斜杠、`Accept` 头要求、会话初始化） | 挂载方式不对会静默失败 | P0 spike + 官方 client SDK 端到端测试 |
| OAuth 2.1 的具体要求（DCR 是否必需、PKCE、授权页交互） | P3 可能返工 | P0 观察「需要认证」的 UI 分支；P3 前单独做一次 OAuth spike |
| 静态 Token 的注入在 Workspace 各端（桌面/移动/云端）一致 | 移动端可能不支持自定义 header | P1 完成后分别在桌面与移动端各验一次 |

本机已确证的事实（无需再验）：WorkBuddy 配置支持 `streamableHttp` + `headers` 的 `${VAR}` 插值（多个官方连接器在用）；支持 `disabledTools`；连接器包为 `mcp.json` + `token-schema.json` + `skills/`；自定义连接器入口在「专家·技能·连接器 → 连接器 → 自定义连接器」；OAuth 为官方一等公民（`token_type: "mcp-oauth"`）。

## 15. 明确不做（YAGNI）

- 员工自助入口（`/my/perf`、`/my/appeal` 不接入 MCP）——用户明确不要
- 文件上传通道（不做 base64 上传、不做一次性上传链接）
- 本系统侧调度器（定时任务交给 WorkBuddy 自动化）
- `delete` / `rerun` / `reset-all` / 密码重置等破坏性操作
- 直接写 `AdjustRecord`（找平写表仍由现有流程自动完成，不经 MCP）
- Token 月度配额拦截（P1 仅记录用量，不拦截）
- 官方连接器市场上架（先做自定义连接器，上架另议）
