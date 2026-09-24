# WorkBuddy × 巡店结算系统 · P1 写工具设计（Write Tools + Token + Audit + SKILL）

- 日期：2026-09-22
- 分支：`feat/workbuddy-integration`
- 状态：范围决策已获用户确认；待评审
- 上游：`2026-09-21-workbuddy-mcp-integration-design.md`（v3）+ `2026-09-22-workbuddy-p0-connectivity-spike-design.md`（P0，已实现）
- 定位：在 P0（只读服务 + 连接器包骨架）之上，交付**写能力**：出正式表 / 月度重算 / 改单价 + Token 鉴权 + 落库审计 + SKILL 表达形式。**不含上传**（用户确认，延续 v3 口径）。

---

## 1. 已确认决策

| 项 | 决策 | 用户确认 |
|---|---|---|
| 写工具范围 | `finalize` / `rebuild` / `set_per_point` 三个 | ✅ 不含上传（P2 再说） |
| 审计形态 | **落库** `mcp_audit_log`（两阶段写入）+ 管理端只读页 `/mcp-audit` | ✅ |
| 表达形式 | SKILL.md + 工具中文描述精修 | ✅（不做独立守则文档、不拆多技能包） |
| 鉴权 | 静态 Token → **`api_tokens` 表**（P0 的环境变量 Token 升级） | v3 §6.1 延续 |
| 实现边界 | 延续 P0：同仓库独立进程 `mcp_service/`，不动 `app/` 既有行为（原则 1） | ✅ |

**原则延续（P0 spec §1.1）**：P1（现有 web 能力/接口零影响）、P2（标准接入、可复用范式）、P3（客户服务器 + 公网访问、测试在本机）。

---

## 2. 事实基础（P1 盘点结论，全部实测于当前代码）

| # | 事实 | 证据 |
|---|---|---|
| W1 | `finalize` 主体是**单一原子 commit**（flow.py:427 先删本文件 formal 后重插 valid raw，同一事务）；其后 stats/period/dash 各自独立 commit（best-effort） | `flow.py:399-436` |
| W2 | `finalize` **幂等**（先删后插单事务，重复安全）；仅文件粒度；同月跨文件双算靠 rebuild 收敛 | 同上 |
| W3 | `finalize` 阻挡：文件不存在 + **文件粒度 pending 申诉**（flow.py:408-415） | 同上 |
| W4 | 文件涉及月份的推导已有现成实现：`finalize_import` 从 valid raw 推月（:429-432）；`auto_finalize_pipeline` 从**全部 raw** 推月（:636-638）——封账判定直接复用 | 同上 |
| W5 | **上传即自动 finalize**：`files_r.py:93-97` 与手动按钮调**同一个** `auto_finalize_pipeline`；MCP 写工具与网页走同一链路 | `files_r.py` |
| W6 | `rebuild_month` **非单事务、多个 commit**：judge 每文件 :326 → **删后 :510** → 插后 :537 → stats :339/:263。**:510 与 :537 之间崩溃 = 整月正式表被清空** | `flow.py:439-545` |
| W7 | **源 raw 空守卫：没有**——raw=0 而 formal>0 时删除照跑（本地库 2026-08 正是此状态） | `flow.py:507-509` |
| W8 | `rebuild` **只 sync 月统计，不同步找平表** `payroll_period_rows` → 重算后找平陈旧 | 同上 |
| W9 | `rebuild` 无快照/备份机制 | 同上 |
| W10 | `set_month_per_point` 重算：该月 `month_perf_records.salary`（走 `_bonus_cfg` 奖金）+ 该月 `payroll_period_rows` 全量（adjust 自动写、prev_adjust 链式递延）；**只算目标月，M+1 结转陈旧**；`AdjustRecord` 锁存单价不受影响 | `perf.py:372-386` |
| W11 | **配置缓存坑确认**：`_CONFIG_CACHE` 进程级全局；`set_month_per_point` 不 warm 不清缓存；`warm_config` 只在 4 个 HTML 路由调用（settle_r.py:164/308/424/966）。冷缓存下奖金按 env 默认 68/3000 而非 sys_configs（2026-09=75/1250）→ **静默写错钱** | `perf.py` |
| W12 | `set_per_point` **无守卫**：无封账、服务层无月份格式校验（非法月静默 ok rows=0）、无确认语；幂等可重试 | `perf.py:372` |
| W13 | `judge_import` 单 commit（:326），返回键 `valid/master_late/from_sub/cross_file_dup/no_ref/blank` | `flow.py:248-327` |
| W14 | `app/db.py`：`SessionLocal = sessionmaker(expire_on_commit=False)`；`get_db` yield+close，无自动 commit | `app/db.py:44-52` |
| W15 | P0 的 `sealed_months` / `rebuild_snapshots` / `mcp_audit_log` / `api_tokens` **全部 0 实现**（仅设计文档） | 全仓 grep |

**MCP 语义推导**（由 W1–W12 直接推出）：

- `finalize`：原子可安全重试 → 异常信封可用普通 `INTERNAL` + 可重试 hint。
- `rebuild`：非原子 + 无守卫 + 无快照 → **不可盲目重试**；必须先补源守卫（W7）、快照（W9）、preview 前置、确认语，错误信封一律 `INTERNAL_WRITE`（禁止自动重试）。
- `set_per_point`：幂等可重试，但**必须先 `ensure_config_warmed`**（W11），且自己补月份校验/封账/确认语（W12）。

---

## 3. 边界

### 3.1 做

- 三个 MCP 写工具 + 只读 `visit_rebuild_preview`（预演，只读估算）。
- `api_tokens` 表 + `/my/token` 签发页 + `Actor` 鉴权接缝。
- `mcp_audit_log` 两阶段审计 + `/mcp-audit` 只读页（含导航入口）。
- `sealed_months` 封账表（**迁移预置 2026-08**）+ `/config` 页封账管理（admin + CSRF）。
- 服务层两处守卫/证据改动（改动 A 源守卫、改动 B 快照）+ **MCP rebuild 成功后补 `sync_period_table`/`sync_dash_metrics`**。
- SKILL.md 表达形式升级 + 三工具中文描述。
- 文档更新（`docs/索引.md`、`AGENTS.md`）与测试（§12）。

### 3.2 不做

- **不改**现有 HTML 路由行为与返回语义（原则 1）——`rebuild` 的 period 补充同步**只加在 MCP 路径**，网页行为不动。
- **不做上传通道**（用户确认，P2）。
- 不做 OAuth（接缝保留即可）；不做员工 Token（只签 admin）。
- 不做封账回灌、不做快照范围扩展（raw/日统计/发薪表不在快照内，明确表述为"正式表可回灌 + 全链路留痕"）。
- 不做限流（沿 v3 §7.6 结论：先不做，审计可观测）。

---

## 4. 架构与接缝

```
WorkBuddy 桌面端 → 连接器包(mcp.json, Bearer ${VISIT_TOKEN})
   → 客户服务器 nginx /mcp → mcp_service/（独立进程, --workers 1）
       → app/api 层能力函数 f(db, actor, **params)   ← 唯一真相（新增 actor）
       → app/services/*（仅改动 A/B 两处守卫）
       → DB（SQLite 本地 / PG 线上）
```

- 能力函数签名统一 `f(db, actor, **params)`（P0 的 `month_summary(db, month)` 在 P1 补上 `actor`——P0 是有意省略，此处显式声明补齐，不留静默继承）。
- `actor`：`{uid, role, scopes}`，由 Token 鉴权模块构造；写工具检查 `role==admin && "write" in scopes`。
- 每个写工具入口先 `ensure_config_warmed(db, month)`（W11 的强制对策）。

---

## 5. Token 鉴权（api_tokens）

### 5.1 表结构（v3 §6.1 延续）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `user_id` | Integer FK → users.id | 绑定到人 |
| `name` | VARCHAR(128) | 用途备注 |
| `token_prefix` | VARCHAR(16) **唯一索引** | Token 明文前 8 位，O(1) 定位 |
| `token_hash` | VARCHAR(64) | `sha256(token)` 十六进制 |
| `scopes` | VARCHAR(64) | `read` 或 `read,write` |
| `created_at` / `last_used_at` / `revoked_at` | DateTime | |

- 用 SHA-256 摘要而非 argon2：token 是 256 位高熵随机串，无字典攻击风险；逐行 argon2 校验是廉价 DoS 向量（v3 §6.1）。
- **只签 admin**；staff 不签发（管理数据本质是管理数据）。若库中存在 staff token（不应有），所有能力函数一律 `FORBIDDEN_TOOL`。

### 5.2 签发页 `/my/token`

- 服务端 in-handler 强制 admin（沿用管理路由写法）；仅允许给自己签发；明文只显示一次。
- POST 必须 CSRF（凭据签发端点，不能只靠登录态）。
- 吊销：置 `revoked_at`，校验一并检查。
- **无需改 `STAFF_ALLOWED`**：中间件只对 staff 生效，admin 访问不受影响（P0 已确认）。

### 5.3 鉴权流程（mcp_service/auth.py 扩展）

`Authorization: Bearer <token>` → 取前 8 位查 `token_prefix` → 校验摘要且未吊销 → 更新 `last_used_at` → 构造 `Actor`。失败一律 401 + `WWW-Authenticate: Bearer`，**认证失败同样落审计**（§8）。

**P0 环境变量 Token 的处置**：P1 起主路径走 api_tokens；`VISIT_MCP_TOKEN` 保留为**未初始化时的 bootstrap**（首次启动、表为空时），并在启动日志提示"已用 bootstrap Token，请尽快在网页签发正式 Token"。

---

## 6. 写工具契约

统一信封（沿 P0）：`{ok:true, data}` / `{ok:false, error:{code,message,hint}}`；结果同时给 `structuredContent` + 文本。

### 6.1 工具清单

| # | 工具 | 读/写 | 前置 | 封账 | 源守卫 | 幂等重试 |
|---|---|---|---|---|---|---|
| 1 | `visit_rebuild_preview(month)` | 读 | — | 是(提示) | 是(只读估算) | 安全 |
| 2 | `visit_finalize_file(file_id)` | 写 | — | **是（按文件推导月份集合，任一命中即拒）** | — | **安全**（原子，W1） |
| 3 | `visit_rebuild_month(month, preview_id, confirm_text)` | 写 | **preview 前置**（§6.3） | 是 | 是（改动 A） | **禁止自动重试**（W6） |
| 4 | `visit_set_per_point(month, per_point, confirm_text)` | 写 | `ensure_config_warmed`（W11） | 是 | — | 安全（幂等，W12） |

### 6.2 逐工具契约

**`visit_rebuild_preview(month)`**（只读，P1 不删除）
- 只读估算，不调 `rebuild_month`。返回（键名对齐 `judge_import` 真实统计键）：
  `formal_rows_now` / `formal_points_now`、`raw_total` / `raw_by_status{valid,cross_file_dup,master_late,from_sub,no_ref,blank}`、`estimated_insert_rows`（=valid）、`dedup_sub_hits`、`affected_persons`、`sealed`（封账时返回 `MONTH_SEALED` 错误）、`preview_id` / `expires_at`、`notes`（估算基于当前 clean_status，重判可能改变分类）。
- 一致性断言（沿 v3 §13）：`行数 == Σ分类计数`、`总点数 == Σ(点数×该点数行数)`——点数可为 0..9，不钉数值。

**`visit_finalize_file(file_id)`**
- 封账判定：从该文件**全部 raw** 推导月份集合（复用 `auto_finalize_pipeline` 的现成推导，W4），任一命中封账月 → `MONTH_SEALED`（错误里列出命中月份）。
- pending 申诉（文件粒度）→ `PENDING_APPEALS`（hint：申诉功能未开放，联系维护人员）。
- 幂等：原子先删后插，安全重试。
- **副作用如实告知**（hint）：stats/period/dash 是 best-effort 各自 commit，若失败提示"部分同步可能未完成，请用只读工具核对"。

**`visit_rebuild_month(month, preview_id, confirm_text)`**
- 前置校验（与会话无关，服务端强制）：
  1. `preview_id` 存在、ok=True、`tool=="visit_rebuild_preview"`、**同一 token_id**、同一 month、created_at 在 **30 分钟窗**内 → 否则 `PREVIEW_REQUIRED`；
  2. `confirm_text == "确认重算 {month}"` → 否则 `CONFIRM_REQUIRED`；
  3. 源守卫（改动 A）：该月 raw=0 而 formal>0 → `NO_SOURCE_ROWS`；
  4. 封账：该月命中 → `MONTH_SEALED`。
- 执行：服务层 `rebuild_month`（含改动 B 快照）→ **成功后补 `sync_period_table(month)` + `sync_dash_metrics`**（W8 缺口，仅 MCP 路径）。
- 异常信封：`INTERNAL_WRITE`，**禁止自动重试**（W6：删后崩溃 = 空月；提示"先观察，用只读工具核对，必要时找维护人员"）。

**`visit_set_per_point(month, per_point, confirm_text)`**
- 前置：`ensure_config_warmed(db, month)`（W11 强制，防写错钱）；`confirm_text == "确认改单价 {month} {per_point}"`；封账闸门。
- 月份校验用 MONTH_PATTERN（W12：服务层不校验，MCP 必须自己挡非法月）。
- 幂等：同值重跑安全。
- **副作用如实告知**（hint）：只重算目标月，**M+1 递延结转会陈旧**，如需对齐请对下月重跑或人工核对。

### 6.3 preview 前置的会话无关性

所有闸门（preview_id、sealed、source guard、confirm、warm）**均与会话无关**（沿 P0 §7 结论），因此 `--workers 1` 或将来 `stateless_http=True` 下语义一致，不依赖进程内状态。

### 6.4 错误码全集（P1 新增）

| code | 触发 | hint |
|---|---|---|
| `UNAUTHORIZED` | 无/坏/吊销 Token | （沿 P0） |
| `FORBIDDEN_TOOL` | role 非 admin / scope 无 write | "该操作仅限管理员且需要写权限 Token" |
| `MONTH_SEALED` | 命中封账月（finalize 列月份） | "该月已封账不可写入；如需修正走对账找平流程" |
| `NO_SOURCE_ROWS` | raw=0 且 formal>0 | "该月源记录已清理，重算会清空正式表，已拒绝" |
| `PREVIEW_REQUIRED` | 缺/过期/不匹配 preview_id | "先调 visit_rebuild_preview(month=...)，把结果复述给用户后用返回的 preview_id 重算" |
| `CONFIRM_REQUIRED` | 缺/错 confirm_text | "请复述确认语：确认重算 2026-09" |
| `PENDING_APPEALS` | 文件有 pending 申诉 | "该文件有未决申诉，申诉功能未开放，请联系维护人员" |
| `NOT_FOUND` | file_id 不存在 | "先调 visit_file_list(month=...) 获取正确 id" |
| `INTERNAL` | 只读工具未预期异常 | （沿 P0，可重试） |
| `INTERNAL_WRITE` | **写工具**未预期异常 | "可能已部分生效（重算在流程中间提交），**不要自动重试**；先用只读工具核对当前状态" |

---

## 7. 闸门实现

| # | 闸门 | 实现位置 | 拦截 |
|---|---|---|---|
| 1 | 认证 | `mcp_service/auth.py`：Bearer → 查表 → `Actor`；失败 401 并审计 | 未授权访问 |
| 2 | 角色/scope | 能力层入口：`actor.role==admin && "write" in actor.scopes` | 员工越权、只读 Token 写数据 |
| 3 | 封账 | `sealed_months` 表 + 能力层 `assert_not_sealed(db, months)`（**支持月份集合**，供 finalize） | 改已封账月 |
| 4 | 源守卫 | 服务层改动 A + 能力层 `assert_has_source(db, month)` | 重算清空整月 |
| 5 | 确认语 | `assert_confirm(text, expect)` | 降低误触（**非安全边界**，诚实定位：agent 能自己填对这句话） |
| 6 | 预演前置 | preview_id 校验（同 token/同月/30 分钟窗/ok=True） | 未看影响面就重算 |
| 7 | 配置就绪 | `ensure_config_warmed(db, month)` | 用错奖金参数写错钱（W11） |
| 8 | 审计 | 两阶段写 `mcp_audit_log` | 不可追溯 |

`disabledTools` 定位（沿 v3 §5/§7.5）：**客户端配置层的开关**（降低误触、收敛工具空间），**不是服务端安全边界**——服务端靠闸门 1–7 兜底，`disabledTools` 不在服务端断言。

---

## 8. 审计（两阶段）

`mcp_audit_log`（沿 v3 §9）：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `token_id` / `user_id` | Integer nullable | 认证失败为 NULL |
| `tool` | VARCHAR(64) nullable | 401 时 NULL |
| `params_json` | TEXT nullable | 入参（脱敏，不含 Token 明文） |
| `ok` | Boolean nullable | 执行中 NULL，结束回填 |
| `error_code` | VARCHAR(32) nullable | |
| `detail` | TEXT nullable | 失败 `repr(exc)` 摘要；成功结果摘要 |
| `client_info` | VARCHAR(255) nullable | 客户端上报名称/版本 |
| `duration_ms` | Integer nullable | |
| `created_at` | DateTime | 落行为准 |

**两阶段**：① 请求进入即插行（ok=NULL，拿到 audit_id）→ ② 执行（preview_id/rebuild_snapshots.audit_id 引用该 id）→ ③ 返回前回填 ok/error/duration。401 也走 ①③。

管理端只读页 **`/mcp-audit`**（admin in-handler 校验）：按人/工具/时间筛选，显示 `INTERNAL`/`UNAUTHORIZED` 行与快照关联；导航加入口。P0 的文件日志（requests.jsonl）保留作调试证据，与 DB 审计并存。

---

## 9. 服务层改动（仅两处守卫 + MCP 路径补同步）

**改动 A · 源数据守卫**（`flow.py` `rebuild_month` 开头，服务层保护所有调用方）：
该月 `raw_records` 计数为 0 而 `formal_records` 计数 > 0 → 直接拒绝（返回 `{"ok":False,"msg":"该月无源记录(raw=0)但有正式表(N)条，拒绝重算以免清空"}`）。正常路径行为不变。

**改动 B · 重算前证据留存**（`flow.py` `rebuild_month` 删除前）：
把该月 `formal_records` 全量序列化存 `rebuild_snapshots`（month / audit_id nullable / payload_json / row_count / created_at；保留策略：按月份留最近一次，超出由维护脚本清理）。
**能力边界写清**：快照**只能回灌正式表**；同一次调用还会改写 clean_status、翻 from_sub、重算 stats/period（MCP 路径补的 period sync），因此是"正式表可回灌 + 全链路留痕"，**不是整月可逆**。写进 SKILL.md 与 `/mcp-audit` 页说明。

**MCP 路径补同步**（不进服务层，避免影响 HTML 行为——原则 1）：`visit_rebuild_month` 成功后调 `period.sync_period_table(month)` + `dashboard.sync_dash_metrics`。HTML 的 `/month/rebuild` 行为不变（其陈旧问题属既有行为，不在本计划范围内改）。

---

## 10. SKILL 表达形式

`deploy/connector/visit-settle/skills/visit-settle/SKILL.md` 升级：

- **写操作规则**（新增）：
  1. **先查后写**：任何写操作前先用只读工具确认当前状态；
  2. 重算前必须调 `visit_rebuild_preview`，**把影响面（当前行数/点数 → 预计）复述给用户**，用返回的 `preview_id` 继续；
  3. `confirm_text` 必须原文（`确认重算 {month}` / `确认改单价 {month} {per_point}`），这是用户的最后确认，不要替用户编造；
  4. **写操作失败不要自动重试**——先观察，用只读工具核对，必要时联系维护人员；
  5. 封账月（2026-08）不可写入。
- **系统口径**（沿用 P0 + 补）：月份 `YYYY-MM`；点数 1/2 点；每点单价与奖金按月可配（9 月 75/1250）；找平正负含义；重算快照只能回灌正式表。

工具描述精修：每个工具写明"做什么 + 什么时候用 + 关键约束 + 依赖的前置工具"。

---

## 11. 数据与迁移

| 迁移（12 位十六进制、链式，`alembic heads` 定 down_revision） | 内容 |
|---|---|
| `<hex>_workbuddy_api_tokens.py` | `api_tokens`（含 `token_prefix` 唯一索引） |
| `<hex>_workbuddy_audit_seal_snapshot.py` | `mcp_audit_log`、`sealed_months`（**INSERT 2026-08**）、`rebuild_snapshots` |

- 两 dialect 都过（SQLite 本地 / PG 线上；TEXT 列不进唯一键，沿 AGENTS.md 已知坑）。
- 服务层改动 A/B 不需要迁移（纯代码守卫/证据）。
- 网页端新增：`/my/token`（账号页）、`/config` 封账小节、`/mcp-audit` 页——路由新增不改既有路由。

---

## 12. 测试策略

| 层 | 方式 | 关键断言 |
|---|---|---|
| 能力层 | pytest + 既有空内存库 fixture + 构造数据 | 权限矩阵（staff 全拒、只读 Token 调写工具拒）、封账（**finalize 多月份推导**：文件跨封账月即整体拒绝并列出月份）、`NO_SOURCE_ROWS`（formal 行数不变）、`CONFIRM_REQUIRED`、`PREVIEW_REQUIRED`（缺/过期/换 token/换月）、`INTERNAL_WRITE` 兜底、幂等重放（finalize 两次调用结果一致） |
| 冷缓存 | 清 `perf._CONFIG_CACHE` 后走 `set_per_point` 路径 | 取到 sys_configs 的 75/1250 而非 env 默认 68/3000（W11） |
| 审计 | 调用后查 `mcp_audit_log` | 成功/失败各一行；401 一行（tool=NULL）；params 无 Token 明文；audit_id 执行前已存在 |
| 快照 | 真跑重算前 | `rebuild_snapshots` 一行且 row_count == 重算前该月 formal 行数；HTML 路径 audit_id 为 NULL 不报错 |
| 一致性 | preview 返回 | `行数 == Σ分类计数`、`总点数 == Σ(点数×行数)` |
| e2e | 官方 client SDK 起真实进程（延续 P0 模式） | 预演 → 确认 → 重算 → 只读核对的完整闭环；错误 Token 401 且审计有行 |

**开发期进展门**：能力层 + 审计 + e2e 全绿后，人工验收才可归因到客户端侧。

---

## 13. 验收标准

1. WorkBuddy 里让 agent 完成一次"**预演 → 复述影响 → 确认 → 重算 → 只读核对**"闭环，数字与 SQL 直查一致；
2. 只读 Token 调写工具被拒（`FORBIDDEN_TOOL`），客户端可见拒绝；
3. **封账月写操作被拒**且 2026-08 数据未变（含跨封账月的文件 finalize 被拒并列出月份）；
4. 无 preview_id 的重算被拒；`raw=0` 的月份重算被拒且正式表未变；
5. 重算前有快照（row_count 一致）；重算后找平表已刷新（W8 补 sync 生效）；
6. `/mcp-audit` 可见全部调用（含 401），按人/工具可筛选；
7. `app/` 既有测试全绿（改动 A/B 不改变正常路径行为）；`mcp_service/tests` 新增用例全绿。

---

## 14. 与 v3 spec 的关系

v3 §4.2 的 #9–#11 工具、§6.1 api_tokens、§7 闸门、§9 审计在本设计中**保留其语义**，差异：

| # | v3 原文 | 本设计 |
|---|---|---|
| 1 | 同进程挂载 /mcp | P0 已改为独立进程（`mcp_service/`），本设计延续 |
| 2 | `--workers 2` 会话议题（§7.6） | 独立进程 `--workers 1` + 闸门全会话无关，议题消解；`stateless_http` 已有（P0 F10） |
| 3 | 上传留网页 | 延续（用户确认 P1 不含上传，P2 再议"网页可下掉"缺口） |
| 4 | preview_id / 确认语 / 封账 | 语义保留，实现落在 `mcp_service/` 能力层 + 服务层改动 A/B |
| 5 | rebuild 后找平刷新 | **新增**：MCP 路径补 `sync_period_table`/`sync_dash_metrics`（v3 未覆盖 W8 缺口） |

**P0 spec §12.1 修订项的落点**：除"上传"外全部在本设计中落实（独立进程、封账独立表、stateless_http、D2）。

---

## 15. 风险

| 风险 | 处理 |
|---|---|
| rebuild 非原子（删后崩溃 = 空月） | 源守卫（改动 A）+ 快照（改动 B）+ preview 前置 + `INTERNAL_WRITE` 禁重试，四层降低触发概率；快照保证正式表可回灌 |
| 未 warm 写错钱（W11） | `ensure_config_warmed` 强制 + 冷缓存测试 |
| 重算后找平陈旧（W8） | MCP 路径补 sync（§9），验收项 5 验证 |
| pending 申诉死锁 | 文件有 pending 申诉 → `PENDING_APPEALS` + 提示联系维护人员（申诉页面已删除，无法在 MCP 侧解决） |
| 客户误触写工具 | 连接器包 `disabledTools` 默认关闭（验收通过后打开）；确认语降低误触（非安全边界） |
| 快照误读为"整月可逆" | SKILL.md 与 /mcp-audit 页明确"只能回灌正式表，非整月可逆" |

---

## 16. 明确不做（YAGNI）

- 上传通道（P2）；员工 Token；OAuth（接缝保留）；限流；快照范围扩展；封账回灌；`delete`/`rerun`/`reset-all`/密码重置/直接写 `AdjustRecord`（C 档，永不给 Agent）。
- 不改 HTML 的 rebuild/找平行为（原则 1，网页陈旧问题不在本计划范围）。
