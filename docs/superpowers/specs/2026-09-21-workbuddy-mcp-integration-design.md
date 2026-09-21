# 巡店结算系统 × WorkBuddy 接入设计（WorkBuddy MCP Integration Design）

- 日期：2026-09-21（v2，按 spec 评审意见全面修订）
- 状态：定稿草案（待用户审阅）
- 配套：`docs/索引.md`、`docs/结算主流程设计-v3.md`、`AGENTS.md`
- 核心思路（用户提出）：**把巡店结算系统变成 WorkBuddy 能调用的能力**——在 WorkBuddy 的工作台里用自然语言查询和操作本系统，而不是每次打开浏览器登录网页。
- v2 修订摘要：① 删除有致命缺陷的 `dry_run` 设计，改为"只读估算 + 源数据守卫 + 落库快照"；② 新增"源 raw 为空即拒绝重算"守卫（评审外的新发现）；③ 补齐逐工具授权矩阵；④ 解决"不做员工自助"与 staff token 的矛盾（仅签发 admin）；⑤ Token 改用 SHA-256 + 前缀索引；⑥ 封账改存 `sys_configs`；⑦ 补 Alembic 迁移与列类型；⑧ 处理 `--workers 2` 与会话亲和；⑨ 修掉基准测试与陈旧路由引用；⑩ 范围收敛为 P0–P2。

---

## 1. 目标与原则

**目标**：WorkBuddy（腾讯全场景 AI 办公工作台）通过 MCP 协议调用本系统的业务能力，覆盖三类场景：

| 场景 | 说明 |
|---|---|
| 只读查询与问答 | "9 月正式表多少人/总点数""某人这个月绩效多少""对账差异有哪些店" |
| 管理端全流程执行 | 口述任务让 Agent 跑：判定 → 出正式表 → 绩效 → 对账 → 找平（上传除外，见 §2） |
| 定时自动化与推送 | 每月出表后自动汇总推送、对账差异告警（调度在 WorkBuddy 侧，本系统零调度代码） |

**原则**：

1. **能力层唯一真相**：业务能力只在 `app/api/` 实现一次，MCP 只是协议适配器。未来接企微助理、定时任务复用同一批函数。
2. **服务端说了算**：权限、封账、只读/写边界全部在服务端强制。WorkBuddy 的执行环境不在本系统信任边界内，**不依赖提示词约束 Agent**。
3. **薄封装**：服务层已是 `{"ok": bool, ...}` 的 dict 契约（`flow.finalize_import` 注释明确"幂等：先删后插，可安全重复执行"；`rebuild_month` 返回 `formal_before/formal_after/points_before/points_after/files`），能力层基本不做转换。
4. **可追溯**：每次工具调用落库审计。
5. **不新增破坏面**：本设计对既有服务层的改动只有两处守卫/快照，且都不改变正常路径的行为（§4.3）。
6. **YAGNI**：不做员工自助、不做上传通道、不做调度器（详见 §16）。

## 2. 已确认的需求决策

| 决策项 | 结论 |
|---|---|
| 目标场景 | 只读问答 + 全流程执行 + 定时推送 |
| 运行形态 | **远端 MCP 挂在 `store-prod`**，与现有 FastAPI 同进程（多 worker 问题见 §7.6） |
| 身份终态 | 每人各自绑定，可审计到人 |
| 鉴权分期 | **P1 用 Per-user Token，OAuth 后置到 P3**（P3 另立 spec，见 §14） |
| 上传口径 | **上传留在网页，MCP 管传完之后的全流程**（远端服务拿不到本地 Excel；上传本就该人工确认文件归属） |
| 写操作范围 | **三项**：出正式表、月度重算、改单价 |
| **申诉** | **用户决定：功能状态未定，MCP 先绕开**——不提供任何申诉工具（§4.5 记录其副作用与风险） |
| Token 持有者 | **仅 admin**（与"不做员工自助"一致，消除 v1 的自相矛盾，见 §6.1） |
| 实施范围 | **P0–P2 一个计划**；P3（OAuth）、P4（自动化推送）各自另立 spec |

## 3. 架构与分层

```
WorkBuddy（桌面 / 移动端 / 云端沙箱）
  │   MCP streamable-http    POST /mcp    Authorization: Bearer <每人一枚的 Token>
  ▼
app/mcp/        适配层：工具声明(schema + 中文描述) · 异常兜底 · 审计
  ▼
app/api/        能力层：纯函数 (db, actor, **params) -> {"ok": bool, ...}   ← 唯一真相
  ▼
app/services/*  既有业务逻辑（仅两处守卫/快照改动，见 §4.3）
  ▼
DB（本地 SQLite / 线上 PG）
```

| 层 | 做什么 | 怎么用 | 依赖 |
|---|---|---|---|
| `app/api/` | 一个业务能力一个函数：入参校验、权限、闸门、调服务层、归一化返回 | `from app.api import perf as api_perf`<br>`api_perf.month_summary(db, actor, month="2026-08")` | `app/services/*`、`app/models.py`、`app/api/guards.py` |
| `app/api/guards.py` | 权限判定、封账判定、月份格式校验、`confirm_text` 校验、异常包装 | 被 `app/api/` 各函数调用 | `app/config.py`、`app/models.py`、`app/db.py` |
| `app/api/tokenauth.py` | Token 解析 → `Actor`（**新模块，不复用 `app/auth.py`**，后者是会话 cookie 与密码哈希） | 被 `app/mcp/` 调用 | `app/models.py` |
| `app/mcp/` | 声明工具、转调能力层、写审计、异常兜底 | 由 WorkBuddy 调用；本地测试用 MCP client SDK | `app/api/` |
| `app/services/*` | 既有业务逻辑 | 不变（除 §4.3 两处） | — |

**调用约定（唯一，全文一致）**：能力层函数签名为 `f(db, actor, ...)`——`db` 第一、`actor` 第二，与既有服务层 `(db, ...)` 习惯一致。

**为什么同进程**：复用现有 DB Session、配置、Alembic、鉴权、部署流程；但多 worker 下的会话状态问题必须显式解决（§7.6）。

## 4. 能力层（`app/api/`）

### 4.1 统一约定

- 返回 `{"ok": True, "data": {...}}` 或 `{"ok": False, "error": {"code","message","hint"}}`（§8）。
- 只读函数不得 `commit()`；写函数由服务层负责提交。
- **所有能力函数必须捕获异常并转成错误信封**（§8.2），不允许异常穿透到 MCP 协议层。

### 4.2 工具清单（逐工具授权矩阵）

**矩阵列含义**：`角色` = 允许调用者；`scope` = Token 必须具备的权限；`封账` = 命中封账月时是否拒绝；`源守卫` = 是否检查该月源 raw 是否为空。

| # | 能力函数 / 工具 | 读/写 | 角色 | scope | 封账 | 源守卫 | 复用 |
|---|---|---|---|---|---|---|---|
| 1 | `perf.month_summary` / `visit_month_summary` | 读 | admin | read | — | — | `services/perf.month_perf` + `FormalRecord` |
| 2 | `perf.person_perf` / `visit_person_perf` | 读 | admin | read | — | — | `services/perf.month_perf`、`salary_for`、`bonus_params` |
| 3 | `perf.ranking` / `visit_perf_ranking` | 读 | admin | read | — | — | `services/perf.month_perf` |
| 4 | `recon.status` / `visit_recon_status` | 读 | admin | read | — | — | `services/recon`、`ReconTask`、`ReconResult` |
| 5 | `recon.diff` / `visit_recon_diff` | 读 | admin | read | — | — | `services/recon`、`PersonDailyStat`、`ReconDataRow` |
| 6 | `files.list_` / `visit_file_list` | 读 | admin | read | — | — | `ImportFile`、`RawRecord` |
| 7 | `payroll.rows` / `visit_payroll` | 读 | admin | read | — | — | `services/period.period_rows`、`carry_map` |
| 8 | `rebuild.preview` / `visit_rebuild_preview` | 读 | admin | read | 是 | 是 | 纯读估算（§4.4） |
| 9 | `files.finalize` / `visit_finalize_file` | 写 | admin | write | 是 | — | `services/flow.finalize_import` |
| 10 | `rebuild.month` / `visit_rebuild_month` | 写 | admin | write | 是 | 是 | `services/flow.rebuild_month` |
| 11 | `perf.set_per_point` / `visit_set_per_point` | 写 | admin | write | 是 | — | `services/perf.set_month_per_point` |

**staff 行为（明确写死，不留歧义）**：本设计**不签发 staff Token**。若数据库中存在角色为 `staff` 的 Token（历史遗留或被误建），**所有能力函数一律返回 `FORBIDDEN_TOOL`**，包括只读工具——因为管理端聚合数据（`month_summary` 含全公司口径、`payroll.rows` 含全员工资、`ranking` 含全员排名）本质是管理数据，`staff` 无权读取。这与"不做员工自助"（§16）一致。

### 4.3 服务层改动（仅两处，且不改变正常路径行为）

**改动 A · 源数据守卫（`services/flow.py: rebuild_month` 开头）**

新增判定：该月 `raw_records` 计数为 0 而 `formal_records` 计数 > 0 时，**直接拒绝**并返回 `{"ok": False, "msg": "该月无源记录(raw=0)但有正式表(N)条，拒绝重算以免清空"}`。

理由（已实测的证据）：`rebuild_month` 先按月份 `DELETE` 正式表（`flow.py:507-510` 并立即 commit），再从该月 `raw_records` 重建（`flow.py:525-537`）。**"按月份删"与"按 raw 重建"的前提不一致时，函数会静默清空整月正式表**。本地 `store_settle_live.db` 就是这种状态：`formal_records` 有 2026-08 的 12507 条，但 `raw_records` 只剩 2026-09（8 月 raw 已被清理）——此时对 2026-08 调用重算会得到 0 条。

没有任何合法场景需要"用 0 条源记录重算一个非空月份"，所以这是纯守卫，不是语义变更。守卫放在服务层（而不是只在能力层）是因为它必须保护所有调用方。

**改动 B · 真跑前快照（`services/flow.py` + 新表 `rebuild_snapshots`）**

`rebuild_month` 在删除前把该月全部 `formal_records` 序列化为 JSON 存入 `rebuild_snapshots(month, audit_id, payload_json, row_count, created_at)`。用途：真跑后若发现结果不对，可用维护脚本（§4.5 C 档）回灌，使重算**真正可逆**。

代价：一次插入一行 JSON（8 月约 12507 行、量级几百 KB~1MB），可接受。

### 4.4 已废弃的 v1 设计：`dry_run`

v1 曾设计"`dry_run=True` 时执行完再 `db.rollback()`"，**该设计有致命缺陷且已删除**：`rebuild_month` 不是单事务——它调用的 `judge_import` 自带 `db.commit()`（`flow.py:326`），随后删除正式表并 `db.commit()`（`flow.py:510`），最后插入再 `db.commit()`（`flow.py:537`）。尾部 rollback 会**提交删除、回滚插入**，即 `dry_run` 直接清空该月正式表——恰好是它声称要防止的后果。

**替代方案（本版采用）**：

- **只读估算器** `app/api/rebuild.preview(db, actor, month)`：不调用 `rebuild_month`，只做只读统计——当月现有正式表行数与点数、当月 `raw_records` 按 `clean_status` 的分布、其中 `valid` 的行数（即预计入表上限）、`DEDUP_SUB_STORES` 排除店命中数、涉及人数。返回字段明确标注为**估算**，并说明"重判可能改变 `clean_status`，实际结果以真跑为准"。
- **真跑前置条件**（服务端强制，不靠 Agent 自觉）：`visit_rebuild_month` 要求 `confirm_text` 与预期字面一致（如 `"确认重算 2026-09"`），且必须先有本会话的 `visit_rebuild_preview` 成功调用记录（由审计表判定，§9），否则返回 `PREVIEW_REQUIRED`。
- **快照**（改动 B）保证可逆。

`set_per_point` 同样要求 `confirm_text`：它不仅改单价，还会重算该月全部 `month_perf_records.salary` 并调用 `period.sync_period_table`，**重算该月所有找平的 `adjust_amount` 与 `prev_adjust_amount`**（`perf.py:344-360` → `period.py`）。这是工资口径级别的操作，与重算同级对待。

### 4.5 三层能力清单（含禁用档）

- **A 档（默认开启）**：矩阵 #1–#8。
- **B 档（交付时在连接器配置层 `disabledTools` 关闭，需要时人工打开）**：#9–#11。
- **C 档（永不给 Agent，硬编码不进工具清单）**：`files.delete`、`files.rerun`、`staff.reset-all`、`staff.reset`、密码重置、直接写 `AdjustRecord`、快照回灌脚本。
- **申诉相关（`appeal.*`）**：按用户决定**本版完全不实现**。

**记录在案的副作用**：`finalize_import` 会因"该文件存在 pending 申诉"而拒绝入表（`flow.py:410-415`），`rebuild_month` 同样有 pending 申诉门槛。而申诉的**员工端与管理端页面均已从当前代码删除**（`6d51a9b`：删除 `my_appeal.html`、`confirm_admin.html` 与 settle_r.py 250 行），`services/flow.resolve_appeal` 目前**零调用方**。因此若线上库存在 pending 申诉，MCP 侧将无法出表/重算，且没有界面可清除。**这是本设计的一个已知风险**，处理方式见 §15.2（不在本期解决）。

## 5. MCP 适配层（`app/mcp/`）

**技术选型**：官方 `mcp` Python SDK（新增依赖，锁定版本），StreamableHTTP 传输，挂载路径 `/mcp`。

**工具命名**：统一前缀 `visit_`，避免与用户机器上其他连接器工具撞名（本机已有 263 个官方连接器）。

**工具描述要求**：每个工具的 `description` 用中文写明"做什么 + 什么时候用 + 关键约束"；参数用 JSON Schema 声明类型、`enum`、必填，月份参数用 `pattern: ^[0-9]{4}-(0[1-9]|1[0-2])$`。描述质量决定 Agent 选对工具的概率。

**只读工具在客户端可见、写工具默认隐藏**：`disabledTools` 是**连接器配置层的客户端开关**，作用是降低误触与减少 Agent 的工具空间，**不是安全边界**（与服务端 `scopes`、封账、角色判定不同，见 §7.5）。

## 6. 鉴权设计

### 6.1 P1 · Per-user Token

新表 `api_tokens`：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `user_id` | Integer FK → `users.id` | 绑定到具体的人 |
| `name` | VARCHAR(128) | 用途备注（如"刘畅的 WorkBuddy 桌面端"） |
| `token_prefix` | VARCHAR(16)，**唯一索引** | Token 明文前 8 位，用于 **O(1) 定位行** |
| `token_hash` | VARCHAR(64) | `sha256(token)` 十六进制 |
| `scopes` | VARCHAR(64) | `read` 或 `read,write` |
| `created_at` / `last_used_at` / `revoked_at` | DateTime | 签发/最近使用/吊销 |

**为什么不用 `app/auth.hash_password`（argon2）**：argon2 是**逐行加盐**的，用它对请求 Token 做"查表校验"必须与每一行分别验证——每行数十毫秒、串行，且是廉价的 DoS 向量。Token 是**高熵随机串**（`secrets.token_urlsafe(32)`，256 bit），不存在字典攻击风险，因此用 SHA-256 定长摘要 + 明文前缀索引即可：`token_prefix` 定位唯一行 → 常量时间比较摘要。

**Token 生命周期**：

- 生成：管理员在 `/my/token` 为**自己**签发（明文只显示一次）。页面挂在既有 `/my/password` 同一区域。
- **无需修改 `STAFF_ALLOWED`**：`app/main.py:54-65` 的隔离只对 `role == "staff"` 生效，管理员访问 `/my/token` 不受影响（这也消除了 v1 里"staff 也要生成 Token 但 `/my/token` 不在白名单"的矛盾）。
- 吊销：置 `revoked_at`；校验时一并检查。
- 鉴权流程：`Authorization: Bearer <token>` → 取前 8 位查 `token_prefix` → 校验 `sha256` 摘要、未吊销 → 更新 `last_used_at` → 构造 `Actor`（`uid`、`role`、`scopes`、`token_id`）。
- 失败一律 `401` + `WWW-Authenticate: Bearer`。
- Token 存储：WorkBuddy 侧仅存本机（官方 `token-schema.json` 描述明确"仅存储在本机…不会上传到云端"）。

### 6.2 P3 · MCP OAuth 2.1（另立 spec）

OAuth 2.1 授权服务器（DCR + authorize + token + PKCE + 授权确认页）是一个独立子系统，**不在本计划的实施范围内**。本设计只需保证：能力层与工具层完全不感知鉴权方式——P3 只替换 `app/api/tokenauth.py` 里"解析凭据 → 构造 `Actor`"的实现。`Actor` 的字段（`uid`/`role`/`scopes`/`token_id`）两种方式都能填满。

## 7. 闸门与安全

| # | 闸门 | 实现位置 | 拦截的效果 |
|---|---|---|---|
| 1 | 认证 | `app/api/tokenauth.py`：Bearer → `Actor`；失败 401 | 未授权访问 |
| 2 | 角色/权限 | `app/api/guards.py`：逐工具矩阵（§4.2）+ scope 校验 | 员工越权、只读 Token 写数据 |
| 3 | 封账 | `guards.assert_not_sealed(month)`，数据存 `sys_configs`（§7.4） | 改历史/已封账月 |
| 4 | 源守卫 | `guards.assert_has_source(month)` + 服务层守卫（§4.3 改动 A） | 重算清空整月正式表 |
| 5 | 确认语 | `guards.assert_confirm(confirm_text, expect)` | 降低误触（**非安全边界**，§7.3） |
| 6 | 预演前置 | `visit_rebuild_month` 检查审计表中本会话已有成功的 `visit_rebuild_preview` | 未看影响面就重算 |
| 7 | 审计 | 每次调用写 `mcp_audit_log`（§9） | 不可追溯 |

### 7.1 中间件与路由绕过

`/mcp` 必须**完全绕过** `staff_isolation` 中间件（`app/main.py:33-66`），而不是仅加入 `STAFF_ALLOWED`：MCP 请求基于 `Authorization` 头而非 cookie，若浏览器同时带着 `ss` cookie 请求 `/mcp`，staff 会话会被 302 到 `/my/perf`，MCP 客户端会收到 HTML 而解析失败。

### 7.2 月份格式校验

能力层必须复用 `rebuild_month` 里那道"唯一关口"的严格写法：`re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month)` 且年份限 `1..9998`。`flow.py:457-462` 的注释已说明用 `\d` 会放行全角「２０２６-08」，导致"先删后填 0 行、整月正式表被清空"。

### 7.3 confirm_text 的诚实定位

MCP 协议没有"弹窗等人工确认"的标准能力，Agent 完全可以自己填对这句话，**所以 `confirm_text` 不是安全边界**。它的价值仅限于降低误触概率（缺这句话直接失败）。真正的边界是闸门 1–4。

### 7.4 封账存储：`sys_configs` 而非环境变量

v1 曾用 `SEALED_MONTHS` 环境变量，与仓库既有机制冲突且需重新部署才能改。改为存 `sys_configs` 表：

- 复用既有的 `/config` 页面与 `/config/save` 路由（`app/routers/settle_r.py:179-232`）与 `perf._cfg_row` 读法；
- 新增配置键 `sealed_months`（逗号分隔的 `YYYY-MM` 列表，语义在此固定为"列表内的月份一律禁止写入"）；
- 好处：可在网页改、无需重新部署、改动留在既有配置里。

### 7.5 disabledTools 的定位

连接器配置里的 `disabledTools` 由客户端读取并生效，**不能作为服务端安全断言**（与 `confirm_text` 同理）。它出现在 §5 是作为交付默认值与工具空间收敛手段，**不出现在本节的闸门表里**。

### 7.6 多 worker 与 MCP 会话状态（必须显式决策）

`deploy/entrypoint.sh:5` 目前是 `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2`。MCP 的 StreamableHTTP 会话管理需要**进程内**状态，多 worker 下会出现"worker A 建的会话请求落到 worker B 而不存在"。可选方案（P0 spike 后二选一，并在本文件更新为定论）：

| 方案 | 做法 | 代价 |
|---|---|---|
| a（优先） | 用 SDK 的**无状态 HTTP 模式**（若 SDK 提供 `stateless_http` 之类开关） | 需确认 SDK 能力（P0 查证）；无状态即无会话亲和问题 |
| b | MCP 端点单独进程/容器（`--workers 1` 的独立服务） | 与"不新增容器"原则相悖，但隔离性最好 |
| c | 主应用降为 `--workers 1` | 改动生产并发模型；本系统为内部小流量，代价可接受，但需用户确认 |

**限流**：P1 **不做**限流。若上线后发现问题，用 `mcp_audit_log` 按 `token_id` + 时间窗做 DB 计数即可——这套栈没有 Redis，任何"内存限流"在多 worker 下都是每进程独立的假象，因此**先不做，而不是做一个错的**。

## 8. 返回契约与错误码（写给 Agent 看）

### 8.1 信封与错误码

```json
{ "ok": true, "data": { ... } }
{ "ok": false, "error": { "code": "...", "message": "...", "hint": "..." } }
```

| code | 触发 | hint 示例 |
|---|---|---|
| `UNAUTHORIZED` | 无/坏/已吊销 Token | "请在 WorkBuddy 连接器设置中重新填写 Access Token（来源：登录巡店系统 → 我的 Token）" |
| `FORBIDDEN_TOOL` | 角色或 scope 不足（含 staff） | "该操作仅限管理员且需要写权限 Token，当前 Token 无此权限" |
| `MONTH_SEALED` | 命中封账月 | "2026-08 已封账不可写入；如需修正请走对账找平流程" |
| `NO_SOURCE_ROWS` | 该月 raw=0 但正式表非空（§4.3 守卫） | "该月源记录已清理，重算会清空正式表，已拒绝。如需修数据请让维护人员处理" |
| `PREVIEW_REQUIRED` | 未先调用 `visit_rebuild_preview` | "先调用 visit_rebuild_preview(month='2026-09') 并向用户复述影响，再执行重算" |
| `PENDING_APPEALS` | 出表/重算时存在未决申诉（服务层返回） | "该文件有未决申诉，申诉功能当前未开放，请联系维护人员处理" |
| `BAD_MONTH` | 月份格式非法 | "月份必须是 YYYY-MM，例如 2026-08" |
| `CONFIRM_REQUIRED` | 缺/错 `confirm_text` | "请复述确认语：确认重算 2026-09" |
| `NOT_FOUND` | 文件 id 不存在 | "先调用 visit_file_list(month='2026-09') 获取正确 id" |
| `INTERNAL` | 未预期异常（§8.2） | "系统内部错误，已记录（审计 id=123）。请重试或换用只读工具确认现状" |

### 8.2 异常兜底规则（一句话，必须实现）

**`app/api/` 的每个函数与 `app/mcp/` 的每个工具入口都必须捕获所有异常**（`Exception`），把异常写入审计表（`error_code="INTERNAL"`，同时记录 `repr(exc)`），并返回标准错误信封。任何异常都不得穿透成 MCP 协议层错误——否则 Agent 拿到的是无 `hint` 的裸错误，正是本节要避免的失败模式。编程错误同样如此：应被记录（便于定位），但对 Agent 统一表现为 `INTERNAL`。

## 9. 审计与快照表

**`mcp_audit_log`**

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `token_id` / `user_id` | Integer | 调用者 |
| `tool` | VARCHAR(64) | 工具名（能力函数名） |
| `params_json` | TEXT | 入参（脱敏：不含 Token 明文） |
| `ok` | Boolean | |
| `error_code` | VARCHAR(32) | NULL 表示成功 |
| `detail` | TEXT | 失败时存 `repr(exc)` 摘要；成功时存结果摘要 |
| `client_info` | VARCHAR(255) | MCP client 上报的名称/版本 |
| `duration_ms` | Integer | |
| `created_at` | DateTime | |

**`rebuild_snapshots`**

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `month` | VARCHAR(7) | |
| `audit_id` | Integer FK → `mcp_audit_log.id` | 关联调用 |
| `payload_json` | TEXT | 该月 `formal_records` 全量序列化（用 TEXT 而非定长 String，避免长度限制） |
| `row_count` | Integer | 快照行数 |
| `created_at` | DateTime | |

**落库约定（对齐 `AGENTS.md` 已知坑）**：`payload_json` 这类大文本列用 `TEXT` 并 `nullable=True`；若将来把 `mcp_audit_log.tool` 之类列纳入唯一键，需用 `VARCHAR(255)` 而非 TEXT（MySQL 唯一键限制）。线上 PG、本地 SQLite 两处都要过迁移。

管理端只读页 `/mcp-audit`（管理员可见）：按人/工具/时间筛选，显示 `INTERNAL` 失败与快照关联。

## 10. 集成陷阱（实现时必须处理）

1. **lifespan 必须接上**：`create_app()` 目前没有 `lifespan`（`app/main.py:7-9`），MCP 的 StreamableHTTP 会话管理器需要挂进 ASGI lifespan，否则**静默连不通**。
2. **`/mcp` 完全绕过 `staff_isolation`**（§7.1）。
3. **`--workers 2` 与会话状态**（§7.6）——必须在 P0 spike 定论。
4. **CSRF 与 `require_login` 不可复用**：`csrf_ok(request, csrf_token)` 与 `Depends(require_login)` 是给 HTML 表单用的（`app/routers/settle_r.py:36-41` 可作范例）。MCP 侧鉴权在 `app/api/tokenauth.py` 重做，**不得声称复用了 CSRF**。
5. **月份格式过严格关口**（§7.2）。
6. **源 raw 为空即拒绝重算**（§4.3 改动 A）——本地库已处于该状态（2026-08 有 12507 条正式表、0 条 raw）。
7. **`/v3/*` 是历史别名**：MCP 不暴露 v3 路径。
8. **陈旧路由引用已修正**：`/my/appeal`、`/my/confirm` 在当前代码中已不存在（`6d51a9b`），本设计不引用它们。
9. **线上演示保护**：演示期只签发 `read` scope，写工具保持 `disabledTools` 关闭。

## 11. 连接器包交付物（`deploy/connector/`）

WorkBuddy 的连接器即一个目录（本机 263 个官方连接器均为 `mcp.json` + 可选 `token-schema.json` + 可选 `skills/<name>/SKILL.md`）。

| 文件 | 内容要点 |
|---|---|
| `mcp.json` | `{"mcpServers":{"visit-settle":{"type":"streamableHttp","url":"https://store.visitworld.me/mcp","headers":{"Authorization":"Bearer ${VISIT_SETTLE_TOKEN}"},"timeout":300000,"disabledTools":[写工具名...]}}}`。`timeout` 取 300000ms（5 分钟）而非 60s：`rebuild_month` 需重判该月全部文件并 `sync_month_stats` 覆盖上万行，60s 会超时并诱发客户端重试（重试虽因幂等而安全，但会重复执行昂贵重算） |
| `token-schema.json` | `title`/`description`/`fields[{key:"VISIT_SETTLE_TOKEN", label:"巡店系统 Access Token", type:"password", required:true, placeholder:"登录巡店系统 → 我的 Token → 生成"}]`，把用户引到真实存在的页面 |
| `skills/巡店结算/SKILL.md` | 业务口径指令包：1点/2点与点数口径、每点单价与奖金规则（每满门槛点奖、门槛按月可配）、找平金额正负含义（负=扣款/正=补款）、月份格式必须是 `YYYY-MM`、**先查后写**、写操作必须先复述影响再执行、重算必须先 `visit_rebuild_preview` |

## 12. 数据与迁移

| 迁移 | 内容 |
|---|---|
| `0001_add_api_tokens` | 新表 `api_tokens`（含 `token_prefix` 唯一索引） |
| `0002_add_mcp_audit_and_snapshots` | 新表 `mcp_audit_log`、`rebuild_snapshots` |

- 位置与命名跟随既有 `migrations/versions/*` 约定；`deploy/entrypoint.sh` 已在启动时执行 `alembic upgrade head`，无需额外步骤。
- 本地 SQLite 与线上 PG 两个 dialect 都要能过（`AGENTS.md` 记录的 MySQL TEXT 默认值/唯一键坑）。
- 配置键 `sealed_months` 走既有 `sys_configs` 保存路径（无需迁移），默认空 = 不封账。

## 13. 测试策略

| 层 | 方式 | 关键断言 |
|---|---|---|
| 能力层行为 | pytest + **既有空内存库 fixture**（`tests_web/conftest.py`，`Base.metadata.create_all`），插入少量构造数据 | 权限矩阵（staff Token 全拒、只读 Token 调写工具拒）、封账拒绝、`BAD_MONTH`、`CONFIRM_REQUIRED`、`PREVIEW_REQUIRED`、`INTERNAL` 兜底 |
| 源守卫 | 构造"该月 formal 非空、raw 为空"的库 | 返回 `NO_SOURCE_ROWS`；且 `formal_records` 行数**不变**（重算被拒） |
| MCP 层 | 官方 MCP client SDK 连同进程 `/mcp` | 工具清单与描述完整；无 Token → 401；写工具受 `scopes` 约束（**服务端可测**）；`disabledTools` **不做断言**（客户端配置，不可服务端测试） |
| 审计 | 调用后查 `mcp_audit_log` | 成功/失败各落一行；`params_json` 不含 Token 明文；`INTERNAL` 场景记录 `detail` |
| 快照 | 真跑重算前 | `rebuild_snapshots` 落一行且 `row_count` 等于重算前该月正式表行数 |
| 一致性断言 | 只读工具返回 | `行数 == 1点 + 2点`、`总点数 == 1×一点 + 2×两点`（**不依赖具体数值**，见下） |

**关于"基准数字"契约测试（开放决策 D1，见 §15.1）**：v1 计划把 `12511/16791` 钉进测试，**该计划已撤销**，理由：`tests_web` 的 fixture 是空库、`store_settle_live.db` 有 67MB 且已漂移（实测 2026-08 为 **12507 行 / 16787 点 / 8227 一点 / 4280 两点 / 34 人**，比 `AGENTS.md` 的 12511/16791/8231 各少 4 条一点；且 8 月 raw 已被清理，**无法归因**）。在该决策落定前，测试只做**内部一致性断言**，不钉数值。

## 14. 分期与验收标准

| 期 | 内容 | 验收标准 |
|---|---|---|
| **P0** | 连通性 spike：最小 MCP（`visit_ping` 回声 + 一个读真库的只读工具）+ 错误 Token 负例；同时验证 §7.6 的会话方案 | ① WorkBuddy 能连上并列出工具 ② 正确 Token 可调用 ③ 错误 Token 被拒且行为可见 ④ 记录握手/认证/报错真实表现 ⑤ **定论 worker 与会话方案** |
| **P1** | 能力层 + A 档 8 个工具 + `api_tokens` 与迁移 + `/my/token` + 审计 + 连接器包 + 文档 | WorkBuddy 里问"8 月正式表多少人、总点数多少"得到正确结果（**数值口径与人工验收一致**）；`/mcp-audit` 可见调用记录 |
| **P2** | B 档 3 个写工具 + 封账（`sys_configs`）+ 源守卫 + 快照 + `confirm_text` + 预演前置 | 只读 Token 调写工具被拒；封账月被拒；`raw=0` 的月份被拒且正式表未变；重算前有快照；真跑结果与网页操作一致 |
| **P3** | OAuth 2.1（**另立 spec**） | — |
| **P4** | WorkBuddy 侧定时任务 + 企微推送（**另立 spec**） | — |

**每期完成后**：更新 `docs/索引.md` 与 `AGENTS.md`（仓库强制要求）；发布走既有 rsync + `docker compose build web` 流程，线上无新增端口。

## 15. 风险与开放决策

### 15.1 开放决策

**决策 D1 · 验收基准数字**：`AGENTS.md` 记 2026-08 为 12511/16791/8231，本地库实测 12507/16787/8227。因 8 月 `raw` 已清理，无法归因那 4 条一点。**用户已表示暂缓决定**。在决定前：测试只做内部一致性断言（§13），不做数值钉扎；本设计其余部分不受影响。三个候选：① 以手工/线上为权威，测试用小型快照 fixture 或对线上只读跑；② 改 `AGENTS.md` 迁就本地库；③ 不钉数值，只做一致性断言 + 人工验收。

**决策 D2 · worker 与会话方案**：§7.6 的 a/b/c 三选一，P0 spike 后定论并回写本节。

### 15.2 已知风险

| 风险 | 说明 | 处理 |
|---|---|---|
| pending 申诉死锁 | 出表/重算会因 pending 申诉硬阻断，而申诉页面已从代码删除、无界面可清 | 本期不解决（用户决定绕开申诉）；如线上确有 pending 申诉，出表将返回 `PENDING_APPEALS` 并提示联系维护人员。**上线前需查一次线上库 pending 申诉计数** |
| 重算不可逆 | 即使有快照，回灌需维护脚本人工执行 | 快照 + 源守卫 + 预演前置三层 |
| 写操作超时/重试 | 重算耗时长，客户端超时后重试会重复执行 | 幂等（服务层保证）+ `timeout` 提到 300s + 审计可观测重复调用 |
| 线上演示被改写 | 演示期若误开写工具 | 只发 `read` Token + `disabledTools` 默认关闭；写进 `AGENTS.md` 已知坑 |

### 15.3 未验证假设与验证方式

| 假设 | 风险 | 验证方式 |
|---|---|---|
| WorkBuddy 桌面端能连本机 `127.0.0.1` 的 MCP 端点 | 若客户端把 MCP 流量走云端网关，本地 spike 不成立 | **P0 spike**（不通则临时公网映射） |
| 远端 MCP 挂载与握手细节（路径尾斜杠、`Accept` 头、会话初始化） | 挂载方式不对会静默失败 | P0 spike + 官方 client SDK 端到端测试 |
| SDK 是否支持无状态 HTTP 模式 | 决定 §7.6 方案选择 | P0 spike 查证 |
| 静态 Token 注入在桌面/移动/云端各端一致 | 移动端可能不支持自定义 header | P1 完成后桌面与移动端各验一次 |

**本机已确证的事实**（无需再验）：配置支持 `streamableHttp` + `headers` 的 `${VAR}` 插值（多个官方连接器在用）；支持 `disabledTools`；连接器包为 `mcp.json` + `token-schema.json` + `skills/`；自定义连接器入口在「专家·技能·连接器 → 连接器 → 自定义连接器」；OAuth 为官方一等公民（官方索引含 `token_type: "mcp-oauth"`）。

## 16. 明确不做（YAGNI）

- 员工自助入口与 staff Token（管理端聚合数据本质是管理数据）
- 申诉相关能力（用户决定绕开；且其页面已从代码删除）
- 文件上传通道（不做 base64 上传、不做一次性上传链接）
- 本系统侧调度器（交给 WorkBuddy 自动化）
- `delete` / `rerun` / `reset-all` / 密码重置 / 直接写 `AdjustRecord` / 快照回灌（一律 C 档）
- 限流（P1 不做，理由见 §7.6）
- OAuth 与定时推送（拆为独立 spec）
- 官方连接器市场上架（先做自定义连接器）
