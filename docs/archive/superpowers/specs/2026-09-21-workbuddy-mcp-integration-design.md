# 巡店结算系统 × WorkBuddy 接入设计（WorkBuddy MCP Integration Design）

- 日期：2026-09-21（v3，第二轮评审后修订）
- 状态：定稿草案（待用户审阅）
- 配套：`docs/索引.md`、`docs/结算主流程设计-v3.md`、`AGENTS.md`
- 核心思路（用户提出）：**把巡店结算系统变成 WorkBuddy 能调用的能力**——在 WorkBuddy 的工作台里用自然语言查询和操作本系统，而不是每次打开浏览器登录网页。
- v2 修订：删除有致命缺陷的 `dry_run`（尾部回滚会提交删除、回滚插入 = 清空整月正式表）；新增源 raw 空守卫；补逐工具授权矩阵；只签 admin Token；Token 改 SHA-256 + 前缀索引；封账改存 DB；补迁移与列类型；处理 `--workers 2`；修基准测试与陈旧路由；范围收敛 P0–P2。
- v3 修订：① `sealed_months` 改为**独立表**（`SysConfig` 是固定列单值表，塞不进去）+ 迁移里**预置 2026-08**；② 预览前置改为**与会话无关**（`preview_id` + 同 token + 时间窗），修掉 v2 在无状态模式下的自相矛盾；③ 审计改**两阶段写入**（先插行拿 id，再回填结果），修掉 `rebuild_snapshots.audit_id` 的时序倒置；④ `files.finalize` 的封账判定改为**从该文件涉及月份推导**（该函数无 month 参数）；⑤ **`warm_config` 必须显式调用**——否则会用 env 默认参数（68/3000）覆盖 `sys_configs`（9 月为 75/1250）而写错钱；⑥ 一致性断言改为 `行数 == Σ分类计数`、`总点数 == Σ点数×计数`（点数可为 0..9）；⑦ 迁移命名改 12 位十六进制；⑧ 写工具失败 hint 不得建议重试；⑨ 预览返回字段按 `judge_import` 真实键名固定；⑩ `/my/token` 补服务端 admin 校验与 CSRF。

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
3. **薄封装**：服务层已是 `{"ok": bool, ...}` 的 dict 契约（`flow.finalize_import` 注释明确"幂等：先删后插，可安全重复执行"），能力层基本不做转换。
4. **可追溯**：每次工具调用（含认证失败）落库审计。
5. **不新增静默破坏面**：对既有服务层的改动只有两处守卫/证据留存，都不改变正常路径行为（§4.3）。
6. **YAGNI**：不做员工自助、不做上传通道、不做调度器（§16）。

## 2. 已确认的需求决策

| 决策项 | 结论 |
|---|---|
| 目标场景 | 只读问答 + 全流程执行 + 定时推送 |
| 运行形态 | **远端 MCP 挂在 `store-prod`**，与现有 FastAPI 同进程（多 worker 问题见 §7.6） |
| 身份终态 | 每人各自绑定，可审计到人 |
| 鉴权分期 | **P1 用 Per-user Token，OAuth 后置到 P3**（P3 另立 spec） |
| 上传口径 | **上传留在网页，MCP 管传完之后的全流程**（远端服务拿不到本地 Excel；上传本就该人工确认文件归属） |
| 写操作范围 | **三项**：出正式表、月度重算、改单价 |
| **申诉** | **用户决定：功能状态未定，MCP 先绕开**——不提供任何申诉工具（§4.5 记录其副作用） |
| Token 持有者 | **仅 admin**（管理端聚合数据本质是管理数据，见 §4.2 末段） |
| 实施范围 | **P0–P2 一个计划**，其中 P0（spike + 定论）先独立执行，因其结论可能改写 §7.6；P3、P4 各自另立 spec |

## 3. 架构与分层

```
WorkBuddy（桌面 / 移动端 / 云端沙箱）
  │   MCP streamable-http    POST /mcp    Authorization: Bearer <每人一枚的 Token>
  ▼
app/mcp/        适配层：工具声明 · 两阶段审计 · 异常兜底
  ▼
app/api/        能力层：纯函数 (db, actor, **params) -> {"ok": bool, ...}   ← 唯一真相
  ▼
app/services/*  既有业务逻辑（仅两处守卫/证据改动，见 §4.3）
  ▼
DB（本地 SQLite / 线上 PG）
```

| 层 | 做什么 | 怎么用 | 依赖 |
|---|---|---|---|
| `app/api/` | 一个业务能力一个函数：入参校验、warm_config、权限、闸门、调服务层、归一化返回 | `from app.api import perf as api_perf`<br>`api_perf.month_summary(db, actor, month="2026-08")` | `app/services/*`、`app/models.py`、`app/api/guards.py` |
| `app/api/guards.py` | 权限矩阵、封账判定、月份校验、`confirm_text` 校验、`ensure_config_warmed`、异常包装 | 被 `app/api/` 各函数调用 | `app/config.py`、`app/models.py`、`app/services/perf.py` |
| `app/api/tokenauth.py` | Token 解析 → `Actor`（**新模块，不复用 `app/auth.py`**，后者是会话 cookie 与密码哈希） | 被 `app/mcp/` 调用 | `app/models.py` |
| `app/mcp/` | 声明工具、两阶段审计、异常兜底、转调能力层 | 由 WorkBuddy 调用；本地测试用 MCP client SDK | `app/api/` |
| `app/services/*` | 既有业务逻辑 | 不变（除 §4.3 两处） | — |

**调用约定（唯一，全文一致）**：能力层函数签名为 `f(db, actor, ...)`——`db` 第一、`actor` 第二。

**为什么同进程**：复用现有 DB Session、配置、Alembic、鉴权、部署流程；多 worker 下的会话问题必须显式解决（§7.6），且**所有新增闸门都设计成与会话无关**（§4.4、§7）。

## 4. 能力层（`app/api/`）

### 4.1 统一约定

- 返回 `{"ok": True, "data": {...}}` 或 `{"ok": False, "error": {"code","message","hint"}}`（§8）。
- 只读函数不得 `commit()`；写函数由服务层负责提交。
- **涉钱函数必须先 `ensure_config_warmed(db, month)`**（见 §4.6，这是会写错钱的坑）。
- **所有能力函数必须捕获异常并转成错误信封**（§8.2），不允许异常穿透到 MCP 协议层。

### 4.2 工具清单（逐工具授权矩阵）

`角色` = 允许调用者；`scope` = Token 必须具备；`封账` = 命中封账月时是否拒绝；`源守卫` = 是否检查该月源 raw 是否为空。

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
| 9 | `files.finalize` / `visit_finalize_file` | 写 | admin | write | **是（按推导月份，见下）** | — | `services/flow.finalize_import` |
| 10 | `rebuild.month` / `visit_rebuild_month` | 写 | admin | write | 是 | 是 | `services/flow.rebuild_month` |
| 11 | `perf.set_per_point` / `visit_set_per_point` | 写 | admin | write | 是 | — | `services/perf.set_month_per_point` |

**#9 的封账判定（v2 遗漏）**：`finalize_import(db, import_id, actor_id=None)`（`flow.py:399-436`）**没有 month 参数**，它按该文件全部 raw 涉及的所有月份删后重建正式表并逐月 `sync_month_stats`（`flow.py:429-435`）。因此封账判定必须**从文件推导月份集合**（与 `finalize_import` 内部同一算法：该文件 `clean_status == "valid"` 的 `RawRecord.modified_raw` 前 7 位去重），**只要有一个命中封账月即整体拒绝**，并在错误里列出命中的月份。

**staff 行为（写死，不留歧义）**：本设计**不签发 staff Token**。若库中存在 `role == "staff"` 的 Token，**所有能力函数一律 `FORBIDDEN_TOOL`**，含只读工具——`month_summary`（全公司口径）、`payroll.rows`（全员工资）、`ranking`（全员排名）本质是管理数据。

**`visit_ping`**：仅存在于 P0 spike，不进产品工具清单（P1 起删除）；P0 期间按"只读、admin、需 token"对待并同样落审计。

### 4.3 服务层改动（仅两处，且不改变正常路径行为）

**改动 A · 源数据守卫（`services/flow.py: rebuild_month` 开头）**

该月 `raw_records` 计数为 0 而 `formal_records` 计数 > 0 时**直接拒绝**：`{"ok": False, "msg": "该月无源记录(raw=0)但有正式表(N)条，拒绝重算以免清空"}`。

依据（实测）：`rebuild_month` 先按月份 `DELETE` 正式表并**立即 commit**（`flow.py:507-510`），再从该月 raw 重建（`flow.py:525-537`）。"按月份删"与"按 raw 重建"前提不一致时会**静默清空整月正式表**。本地 `store_settle_live.db` 正是该状态：2026-08 有 12507 条正式表，`raw_records` 只剩 2026-09。没有任何合法场景需要"用 0 条源记录重算一个非空月份"，故为纯守卫。放在服务层以保护所有调用方（含 HTML 的 `/month/rebuild`）。

**改动 B · 重算前证据留存（`services/flow.py` + 新表 `rebuild_snapshots`）**

`rebuild_month` 在删除前把该月 `formal_records` 全量序列化存 `rebuild_snapshots`。

**能力边界必须写清（v2 表述过头，已修正）**：该快照**只能回灌正式表**。同一次调用还会由 `judge_import` 改写 `raw_records.clean_status/confirm_state`（`flow.py:279-326`）、把 `DEDUP_SUB_STORES` 命中的 raw 翻成 `from_sub`（`flow.py:516-537`），并重算 `person_daily_stats → month_perf_records → payroll_period_rows`。**因此它是"正式表可回灌 + 全链路留痕"，不是整月可逆**。扩展快照范围属 YAGNI，本期不做；限制写进 §11 的 SKILL.md 与 `/mcp-audit` 页面说明。

### 4.4 预演（替代 v1 的 dry_run），与会话无关

v1 的 `dry_run`（执行完再 `db.rollback()`）**已废弃**：`rebuild_month` 非单事务——`judge_import` 自带 commit（`flow.py:326`）、删除后 commit（`flow.py:510`）、插入后 commit（`flow.py:537`）。尾部 rollback 会提交删除、回滚插入，即 `dry_run` 自己清空整月表。

**本版设计**：

1. **只读估算**：`app/api/rebuild.preview(db, actor, month)` 不调用 `rebuild_month`，只做只读统计。返回字段（键名对齐 `judge_import` 的真实统计键 `valid` / `cross_file_dup` / `master_late` / `from_sub` / `no_ref` / `blank`）：

   | 字段 | 含义 |
   |---|---|
   | `formal_rows_now` / `formal_points_now` | 该月当前正式表行数/点数 |
   | `raw_total` / `raw_by_status` | 该月 raw 总数与按 `clean_status` 的分类计数（键同上） |
   | `estimated_insert_rows` | `raw_by_status["valid"]`，即预计入表**上限** |
   | `dedup_sub_hits` | `DEDUP_SUB_STORES` 命中行数（重算会被翻成 `from_sub` 而不入表） |
   | `affected_persons` | 该月正式表现有 `person_code` 去重计数 |
   | `sealed` | 该月是否封账（封账时同时返回 `MONTH_SEALED` 错误） |
   | `preview_id` / `expires_at` | 本次预演的审计行 id 与有效期（见下） |
   | `notes` | 固定提示：估算基于当前 `clean_status`，重判可能改变分类，实际以真跑为准 |

2. **真跑前置（与会话无关，服务端强制）**：`visit_rebuild_month` 必须传 `preview_id`，服务端校验：该 id 存在、`ok=True`、`tool == "visit_rebuild_preview"`、**同一 `token_id`**、同一 `month`、`created_at` 在 **30 分钟**窗口内；否则 `PREVIEW_REQUIRED`。这样在 §7.6 的**无状态模式下同样成立**（不依赖任何会话状态）。
3. **确认语**：`confirm_text` 必须等于 `确认重算 {month}`。
4. **证据留存**（改动 B）保证正式表可回灌。

`set_per_point` 同样要求 `confirm_text`（`确认改单价 {month} {value}`）：它不只改单价，还重算该月全部 `month_perf_records.salary` 并调 `period.sync_period_table`，**重算该月所有找平的 `adjust_amount`/`prev_adjust_amount`**（`perf.py:344-360`）。与重算同级对待。

### 4.5 三层能力清单（含禁用档）

- **A 档（默认开启）**：矩阵 #1–#8。
- **B 档（交付时在连接器配置层 `disabledTools` 关闭，需要时人工打开）**：#9–#11。
- **C 档（永不给 Agent，硬编码不进工具清单）**：`files.delete`、`files.rerun`、`staff.reset-all`、`staff.reset`、密码重置、直接写 `AdjustRecord`、快照回灌脚本。
- **申诉相关（`appeal.*`）**：按用户决定**本版完全不实现**。

**记录在案的副作用**：`finalize_import` 因"存在 pending 申诉"拒绝入表（`flow.py:410-415`），`rebuild_month` 同样有该门槛；而申诉的**员工端与管理端页面均已从当前代码删除**（`6d51a9b` 删除 `my_appeal.html`、`confirm_admin.html` 与 settle_r.py 250 行），`resolve_appeal` 目前**零调用方**。若线上库存在 pending 申诉，MCP 侧将无法出表/重算且无界面可清。**上线前必须查一次线上 pending 申诉计数**（§15.2）。

### 4.6 `warm_config`：不调用就会写错钱（v2 遗漏）

`perf.month_per_point` 与 `perf._bonus_cfg` 都"优先系统配置表（须先 `warm_config`）"，缓存为空时**回退到 env/默认值**（`perf.py:76-79, 331-341`）。而 `warm_config` 目前**只在 4 个 HTML 路由被调用**（`settle_r.py:129/189/290/826`），`app/main.py` 没有 lifespan 或中间件钩子。

实测差异：`sys_configs` 为 `2026-08: 250/68/3000`、`2026-09: 250/75/1250`，而 env 默认是 `68/3000`。**MCP 若不 warm，`visit_set_per_point` 会用 68/3000 重算 9 月的 salary/奖金/找平金额——静默写错钱。**

**要求**：

- `app/api/guards.py` 提供 `ensure_config_warmed(db, month)`，内部调 `perf.warm_config(db, month)`；
- 所有涉钱工具（#1、#2、#3、#7、#11，以及 #8 的估算）在入口调用它；`month` 缺省时用当前结算月（与 `/perf` 路由同源的取法）；
- 在 MCP 适配层的**每个请求**入口也先 warm 一次（因 `_CONFIG_CACHE` 是进程级全局，多 worker 下每进程各自 warm）；
- §13 增加"冷缓存"测试：清空缓存后调用涉钱工具路径，断言取到的是 `sys_configs` 值而非 env 默认值。

## 5. MCP 适配层（`app/mcp/`）

**技术选型**：官方 `mcp` Python SDK，StreamableHTTP 传输，挂载路径 `/mcp`。**依赖锁版本**：在 `requirements-web.txt` 中以 `mcp==<x.y.z>` 精确锁定（禁止 `>=`），版本以 P0 spike 中实际验证可用的版本为准，并按 §14 流程回写本节。

**工具命名**：统一前缀 `visit_`，避免与用户机器上其他连接器工具撞名（本机已有 263 个官方连接器）。

**工具描述要求**：中文写明"做什么 + 什么时候用 + 关键约束"；参数用 JSON Schema 声明类型、`enum`、必填，月份用 `pattern: ^[0-9]{4}-(0[1-9]|1[0-2])$`。描述质量决定 Agent 选对工具的概率。

**`disabledTools` 的定位**：它是**连接器配置层的客户端开关**（降低误触、收敛工具空间），**不是安全边界**，因而不出现在 §7 的闸门表里。

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

**为什么不用 `app/auth.hash_password`（argon2）**：argon2 逐行加盐，用它做"查表校验"必须与每行分别验证——每行数十毫秒、串行，是廉价 DoS 向量。Token 是 256 位高熵随机串（`secrets.token_urlsafe(32)`），无字典攻击风险，故用 SHA-256 摘要 + 明文前缀索引：定位唯一行 → 常量时间比较摘要。

**页面与守卫**：

- `/my/token`：**服务端 in-handler 强制 admin**（与既有管理路由同一写法：`accounts_r.py:78`、`settle_r.py:183/210/771` 都显式判 `user.role != "admin"`）；仅允许给**自己**签发，明文只显示一次。
- 签发/吊销的 POST **必须带 CSRF**（复用 `csrf_ok(request, csrf_token)`）；这是凭据签发端点，不能只靠登录态。
- **无需修改 `STAFF_ALLOWED`**：`app/main.py:54-65` 的隔离只对 `role == "staff"` 生效，admin 访问 `/my/token` 不受影响。
- 吊销：置 `revoked_at`，校验时一并检查。

**鉴权流程**：`Authorization: Bearer <token>` → 取前 8 位查 `token_prefix` → 校验 SHA-256 摘要且未吊销 → 更新 `last_used_at` → 构造 `Actor`（`uid`/`role`/`scopes`/`token_id`）。失败一律 `401` + `WWW-Authenticate: Bearer`，**认证失败同样落审计**（§9）。

### 6.2 P3 · MCP OAuth 2.1（另立 spec）

OAuth 2.1 授权服务器（DCR + authorize + token + PKCE + 授权确认页）是独立子系统，不在本计划范围。本设计保证能力层与工具层不感知鉴权方式：P3 只替换 `app/api/tokenauth.py` 中"解析凭据 → 构造 `Actor`"的实现。

## 7. 闸门与安全

| # | 闸门 | 实现位置 | 拦截的效果 |
|---|---|---|---|
| 1 | 认证 | `app/api/tokenauth.py`：Bearer → `Actor`；失败 401（并审计） | 未授权访问 |
| 2 | 角色/权限 | `guards.assert_tool_allowed(actor, tool)`：按 §4.2 矩阵判角色与 scope | 员工越权、只读 Token 写数据 |
| 3 | 封账 | `guards.assert_not_sealed(db, months)`（**支持月份集合**，供 #9 用） | 改已封账月 |
| 4 | 源守卫 | `guards.assert_has_source(db, month)` + 服务层守卫（§4.3 A） | 重算清空整月正式表 |
| 5 | 确认语 | `guards.assert_confirm(text, expect)` | 降低误触（**非安全边界**，§7.3） |
| 6 | 预演前置 | 校验 `preview_id`（同 token/同月/30 分钟窗/`ok=True`） | 未看影响面就重算 |
| 7 | 配置就绪 | `ensure_config_warmed(db, month)`（§4.6） | 用错奖金参数写错钱 |
| 8 | 审计 | 两阶段写 `mcp_audit_log`（§9） | 不可追溯 |

### 7.1 中间件与路由绕过

`/mcp` 必须**完全绕过** `staff_isolation`（`app/main.py:33-66`），而非仅加入 `STAFF_ALLOWED`：MCP 走 `Authorization` 头，若请求同时带 `ss` cookie，staff 会话会被 302 到 `/my/perf`，客户端收到 HTML 而解析失败。

### 7.2 月份格式校验

复用"唯一关口"写法：`re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month)` 且年份限 `1..9998`。`flow.py:457-462` 注释已说明用 `\d` 会放行全角「２０２６-08」，导致"先删后填 0 行、整月正式表被清空"。

### 7.3 confirm_text 的诚实定位

MCP 无"弹窗等人工确认"的标准能力，Agent 完全可以自己填对这句话，**故 `confirm_text` 不是安全边界**，仅降低误触概率。真正的边界是闸门 1–4、6–7。

### 7.4 封账存储：独立表 `sealed_months`（v2 方案已证伪）

v1 用 `SEALED_MONTHS` 环境变量（改一次要重新部署）；v2 改存 `sys_configs`，但**`SysConfig` 是固定列单值表**（`models.py:112-122`：`config_month/per_point/bonus_group/bonus_amount/updated_by/updated_at`，最新一条生效），既塞不进 `sealed_months`，`/config/save` 也只收 3 个字段——所以 v2 的"无需迁移、复用既有页面"两条都不成立。

**本版方案**：新表 `sealed_months`。

| 列 | 类型 | 说明 |
|---|---|---|
| `month` | VARCHAR(7) **PK** | `YYYY-MM` |
| `note` | VARCHAR(255) nullable | 封账原因 |
| `created_by` | Integer nullable | 操作人 |
| `created_at` | DateTime | |

- 封账 = 该表存在该月行；语义固定为"表内月份一律禁止写入"（不做"截止月"这类隐式语义，避免歧义）。
- 管理入口：`/config` 页面新增一小节（列出已封账月份 + 新增/解除），POST 到新路由 `/config/seal`、`/config/unseal`，**admin in-handler 校验 + CSRF**，只写这一张表，不影响 `sys_configs` 的单值语义。
- **迁移中预置 `2026-08`**（`note="8月封账（历史演示口径）"`）。否则上线后无任何月份被保护，一个写 Token 就能把 8 月全部 salary/找平金额重算掉，与 `AGENTS.md`"8 月封账数据不随规则变更"直接冲突（§14 P2 验收项）。

### 7.5 disabledTools 的定位

由客户端读取生效，**不能作为服务端安全断言**（与 `confirm_text` 同理）。故仅出现在 §5，不出现在本节。

### 7.6 多 worker 与 MCP 会话状态

`deploy/entrypoint.sh:5` 现为 `uvicorn ... --workers 2`；MCP 的 StreamableHTTP 会话管理需要进程内状态，多 worker 下"worker A 建的会话请求落到 worker B 会不存在"。**注意：本设计的所有新闸门（§4.4 的 `preview_id`、§7 的 3/4/5/6/7）都已与会话无关**，因此会话方案只影响协议层握手，不影响安全语义。

| 方案 | 做法 | 代价 |
|---|---|---|
| a（优先） | SDK 的**无状态 HTTP 模式**（若有 `stateless_http` 类开关） | 需确认 SDK 能力（P0 查证） |
| b | MCP 端点独立进程/容器（`--workers 1`） | 与"不新增容器"相悖，隔离性最好 |
| c | 主应用降为 `--workers 1` | 改动生产并发模型；本系统内部小流量，需用户确认 |

**限流**：P1 **不做**。这套栈没有 Redis，任何内存限流在多 worker 下都是每进程独立的假象（实际 2 倍额度）；若真出问题，用 `mcp_audit_log` 按 `token_id` + 时间窗做 DB 计数。**先不做，而不是做一个错的。**

## 8. 返回契约与错误码

### 8.1 信封与错误码

```json
{ "ok": true, "data": { ... } }
{ "ok": false, "error": { "code": "...", "message": "...", "hint": "..." } }
```

| code | 触发 | hint |
|---|---|---|
| `UNAUTHORIZED` | 无/坏/已吊销 Token | "请在 WorkBuddy 连接器设置中重新填写 Access Token（来源：登录巡店系统 → 我的 Token）" |
| `FORBIDDEN_TOOL` | 角色或 scope 不足（含 staff） | "该操作仅限管理员且需要写权限 Token，当前 Token 无此权限" |
| `MONTH_SEALED` | 命中封账月（#9 会列出命中的月份） | "2026-08 已封账不可写入；如需修正请走对账找平流程" |
| `NO_SOURCE_ROWS` | 该月 raw=0 但正式表非空 | "该月源记录已清理，重算会清空正式表，已拒绝。请让维护人员处理" |
| `PREVIEW_REQUIRED` | 缺/过期/不匹配的 `preview_id` | "先调用 visit_rebuild_preview(month='2026-09')，把结果复述给用户后再用返回的 preview_id 重算" |
| `PENDING_APPEALS` | 出表/重算时有未决申诉（服务层返回） | "该文件有未决申诉，申诉功能当前未开放，请联系维护人员处理" |
| `BAD_MONTH` | 月份格式非法 | "月份必须是 YYYY-MM，例如 2026-08" |
| `CONFIRM_REQUIRED` | 缺/错 `confirm_text` | "请复述确认语：确认重算 2026-09" |
| `NOT_FOUND` | 文件 id 不存在 | "先调用 visit_file_list(month='2026-09') 获取正确 id" |
| `INTERNAL` | **只读工具**未预期异常 | "系统内部错误，已记录（审计 id=123）。可重试" |
| `INTERNAL_WRITE` | **写工具**未预期异常 | "系统内部错误，已记录（审计 id=123）。**不要自动重试**——该操作可能已部分生效（出表/重算在流程中间就会提交）；请先用只读工具确认当前状态，再与用户确认下一步" |

**为什么区分 `INTERNAL` 与 `INTERNAL_WRITE`**：`rebuild_month` 在删除正式表后**立即 commit**（`flow.py:510`），异常若发生在删除之后，正式表已空。此时诱导 Agent"重试"是危险的。写工具的异常提示必须转向"先观察、勿重试"。

### 8.2 异常兜底规则

**`app/api/` 每个函数与 `app/mcp/` 每个工具入口都必须捕获 `Exception`**，把异常写入审计（`error_code="INTERNAL"`，`detail=repr(exc)` 摘要），并返回标准错误信封；写工具返回 `INTERNAL_WRITE`。任何异常都不得穿透成 MCP 协议层错误——否则 Agent 拿到无 `hint` 的裸错误，正是本节要避免的失败模式。

## 9. 审计（两阶段写入）与证据表

**`mcp_audit_log`**

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `token_id` / `user_id` | Integer **nullable** | 认证失败时无 Token → 两者为 NULL |
| `tool` | VARCHAR(64) **nullable** | 认证失败时记 `NULL`（`error_code=UNAUTHORIZED`） |
| `params_json` | TEXT nullable | 入参（脱敏，不含 Token 明文） |
| `ok` | Boolean **nullable** | 执行中为 NULL，结束后回填 |
| `error_code` | VARCHAR(32) nullable | |
| `detail` | TEXT nullable | 失败存 `repr(exc)` 摘要；成功存结果摘要 |
| `client_info` | VARCHAR(255) nullable | MCP client 上报的名称/版本 |
| `duration_ms` | Integer nullable | |
| `created_at` | DateTime | 落行为准 |

**两阶段写入（修掉 v2 的时序倒置）**：

1. **请求进入即插行**：`ok=NULL`、`created_at` 落当前时间，拿到 `audit_id`；
2. 执行工具（`preview_id` 与 `rebuild_snapshots.audit_id` 都引用这个已存在的 id）；
3. **返回前回填**：`ok`、`error_code`、`detail`、`duration_ms`。

认证失败（401）也走第 1、3 步（`tool=NULL`），满足"每次调用都审计"。

**`rebuild_snapshots`**

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `month` | VARCHAR(7) | |
| `audit_id` | Integer **nullable（不加 FK 约束）** | MCP 路径引用审计行；HTML `/month/rebuild` 路径无审计行故为 NULL |
| `payload_json` | TEXT | 该月 `formal_records` 全量序列化 |
| `row_count` | Integer | |
| `created_at` | DateTime | |

**保留策略**：一行约几百 KB~1MB（8 月 12507 行），重算可重复触发 → 保留最近 N 次（默认 10）或按月份保留最近一次，超出由维护脚本清理；`/mcp-audit` 页面显示可用快照列表。

**落库约定（对齐 `AGENTS.md` 已知坑）**：大文本列用 `TEXT` + `nullable=True`；若要进唯一键则用 `VARCHAR(255)`（MySQL 限制）。本地 SQLite 与线上 PG 都要过迁移。

管理端只读页 `/mcp-audit`（admin in-handler 校验）：按人/工具/时间筛选，显示 `INTERNAL`/`UNAUTHORIZED` 行与快照关联；需在导航加入口。

## 10. 集成陷阱（实现时必须处理）

1. **lifespan 必须接上**：`create_app()` 无 `lifespan`（`app/main.py:7-9`），MCP 的 StreamableHTTP 会话管理器需挂进 ASGI lifespan，否则**静默连不通**。
2. **`/mcp` 完全绕过 `staff_isolation`**（§7.1）。
3. **`--workers 2` 与会话状态**（§7.6）——P0 spike 定论（D2）。
4. **CSRF 与 `require_login` 不可复用**：MCP 侧鉴权在 `app/api/tokenauth.py` 重做，**不得声称复用了 CSRF**；反之 `/my/token`、`/config/seal` 这类网页端点**必须**用 CSRF。
5. **月份格式过严格关口**（§7.2）。
6. **源 raw 为空即拒绝重算**（§4.3 A）——本地库已处于该状态。
7. **必须显式 `warm_config`**（§4.6）——否则用 env 默认（68/3000）覆盖 `sys_configs`（9 月 75/1250）而写错钱。
8. **`finalize` 的封账判定要按文件推导月份集合**（§4.2 #9）——该函数没有 month 参数。
9. **`/v3/*` 是历史别名**：MCP 不暴露 v3 路径。
10. **`STAFF_ALLOWED` 里的 `/my/appeal`、`/my/confirm` 是死字符串**（`main.py:28-29`，路由已删）。不阻塞本设计；顺手清理属可选的小改动。
11. **线上演示保护**：演示期只签发 `read` scope，写工具保持 `disabledTools` 关闭。

## 11. 连接器包交付物（`deploy/connector/`）

WorkBuddy 连接器即一个目录（本机 263 个官方连接器均为 `mcp.json` + 可选 `token-schema.json` + 可选 `skills/<name>/SKILL.md`）。

| 文件 | 内容要点 |
|---|---|
| `mcp.json` | `{"mcpServers":{"visit-settle":{"type":"streamableHttp","url":"https://store.visitworld.me/mcp","headers":{"Authorization":"Bearer ${VISIT_SETTLE_TOKEN}"},"timeout":300000,"disabledTools":[写工具名...]}}}`。`timeout` 取 300000ms：`rebuild_month` 需重判该月全部文件并 `sync_month_stats` 覆盖上万行，60s 会超时诱发重试（幂等但昂贵） |
| `token-schema.json` | `fields[{key:"VISIT_SETTLE_TOKEN", label:"巡店系统 Access Token", type:"password", required:true, placeholder:"登录巡店系统 → 我的 Token → 生成"}]`，引向真实存在的页面 |
| `skills/巡店结算/SKILL.md` | 业务口径：1点/2点与点数口径（**点数可为 0..9，取决于该文件 layout 的 `point_rules`**）、每点单价与奖金规则（每满门槛点奖，门槛/奖额按月可配，9 月为 75/1250）、找平金额正负含义（负=扣款/正=补款）、月份必须是 `YYYY-MM`、**先查后写**、写操作先复述影响、重算必须先 `visit_rebuild_preview`；**写操作失败不要自动重试**；**快照只能回灌正式表** |

## 12. 数据与迁移

| 迁移（文件名 12 位十六进制，沿用既有约定） | 内容 |
|---|---|
| `<12hex>_workbuddy_api_tokens.py` | 新表 `api_tokens`（含 `token_prefix` 唯一索引） |
| `<12hex>_workbuddy_audit_snapshots_seal.py` | 新表 `mcp_audit_log`、`rebuild_snapshots`、`sealed_months`；并 **INSERT `sealed_months(month='2026-08')`** |

- **命名与链式**：`migrations/versions/` 下 30 个既有迁移全部使用 12 位十六进制 revision id（如 `0f3a9c7e2b51_adjust_records_找平表.py`）。新迁移沿用该风格，`revision` 为新的 12 位十六进制，`down_revision` 取**迁移时的当前 head**（由 `alembic heads` 确定；两个新迁移串成链）。
- `deploy/entrypoint.sh` 已在启动时执行 `alembic upgrade head`，无需额外步骤。
- 本地 SQLite 与线上 PG 两个 dialect 都要能过（`AGENTS.md` 记录的 MySQL TEXT 默认值/唯一键坑）。
- `requirements-web.txt` 增加 `mcp==<x.y.z>`（精确锁定，见 §5）。

## 13. 测试策略

| 层 | 方式 | 关键断言 |
|---|---|---|
| 能力层行为 | pytest + **既有空内存库 fixture**（`tests_web/conftest.py`），插入少量构造数据 | 权限矩阵（staff 全拒、只读 Token 调写工具拒）、封账（含 #9 的**多月份推导**：文件跨封账月即整体拒绝）、`BAD_MONTH`、`CONFIRM_REQUIRED`、`PREVIEW_REQUIRED`（缺/过期/换 token/换月）、`INTERNAL`/`INTERNAL_WRITE` 兜底 |
| 源守卫 | 构造"该月 formal 非空、raw 为空"的库 | 返回 `NO_SOURCE_ROWS` 且 `formal_records` 行数**不变** |
| **冷缓存** | 先清空 `perf._CONFIG_CACHE` 再调用涉钱工具路径 | 取到 `sys_configs` 的 75/1250 而非 env 默认 68/3000（§4.6） |
| 审计两阶段 | 调用后查 `mcp_audit_log` | 成功/失败各一行；401 也有一行（`tool=NULL`）；`params_json` 不含 Token 明文；`audit_id` 在执行前已存在（可被快照引用） |
| 快照 | 真跑重算前 | `rebuild_snapshots` 一行且 `row_count` == 重算前该月正式表行数；HTML 路径下 `audit_id` 为 NULL 且不报错 |
| MCP 层 | 官方 MCP client SDK 连同进程 `/mcp` | 工具清单与描述完整；无 Token → 401；写工具受 `scopes` 约束（**服务端可测**）；`disabledTools` 不作断言（客户端配置） |
| 一致性断言 | 只读工具返回 | **`行数 == Σ(raw_by_status 各分类计数)`、`总点数 == Σ(点数 × 该点数行数)`**——不假设点数只有 1/2（`store_settle/rules.point_for` 在 `point_rules` 存在时允许 `0..9`），也不钉具体数值（见下） |

**关于"基准数字"契约测试（开放决策 D1，见 §15.1）**：v1 曾计划把 `12511/16791` 钉进测试，**已撤销**：`tests_web` fixture 是空库；`store_settle_live.db` 有 67MB 且已漂移（实测 2026-08 = **12507 行 / 16787 点 / 8227 一点 / 4280 两点 / 34 人**，比 `AGENTS.md` 的 12511/16791/8231 各少 4 条一点），且 8 月 raw 已被清理**无法归因**。D1 落定前只做上述一致性断言。

## 14. 分期与验收标准

| 期 | 内容 | 验收标准 |
|---|---|---|
| **P0**（独立小计划先跑） | 连通性 spike：最小 MCP（`visit_ping` + 一个读真库的只读工具）+ 错误 Token 负例；查证 SDK 无状态能力与生命周期接法 | ① WorkBuddy 能连上并列出工具 ② 正确 Token 可调用 ③ 错误 Token 被拒且行为可见 ④ 记录握手/认证/报错真实表现 ⑤ **给出 D2 定论（会话方案）并回写 §7.6** |
| **P1** | 能力层 + A 档 8 个工具 + `api_tokens` 迁移 + `/my/token` + 审计 + `/mcp-audit`（含导航入口）+ 连接器包 + `requirements-web.txt` 锁版本 + 文档 | WorkBuddy 里问"8 月正式表多少人、总点数多少"，结果**与同一份库的人工结算口径一致**（D1 未定期间以人工核对为准，不做数值钉扎）；`/mcp-audit` 可见调用记录（含 401） |
| **P2** | B 档 3 个写工具 + `sealed_months` 表与迁移预置 + 源守卫 + 快照 + `confirm_text` + `preview_id` 前置 + `warm_config` 强制 | 只读 Token 调写工具被拒；**封账月 `set_per_point` 被拒且 2026-08 数据未变**；跨封账月的文件出表被拒并列出月份；`raw=0` 的月份重算被拒且正式表未变；无 `preview_id` 的重算被拒；重算前有快照；真跑结果与网页操作一致 |
| **P3** | OAuth 2.1（**另立 spec**） | — |
| **P4** | WorkBuddy 侧定时任务 + 企微推送（**另立 spec**） | — |

**每期完成后**：更新 `docs/索引.md` 与 `AGENTS.md`（仓库强制要求）；发布走既有 rsync + `docker compose build web` 流程，线上无新增端口。

## 15. 风险与开放决策

### 15.1 开放决策

**决策 D1 · 验收基准数字**：`AGENTS.md` 记 2026-08 为 12511/16791/8231，本地库实测 12507/16787/8227（8 月 raw 已清理，无法归因那 4 条一点）。**用户已表示暂缓决定**。在决定前：测试只做一致性断言（§13），P1 验收以"与同一份库的人工结算口径一致"表述（§14），不做数值钉扎。候选：① 以手工/线上为权威（小快照 fixture 或线上只读跑）；② 改 `AGENTS.md` 迁就本地库；③ 只做一致性断言 + 人工验收。

**决策 D2 · worker 与会话方案**：§7.6 的 a/b/c 三选一，P0 spike 后定论并回写 §7.6。**该决策已与安全语义解耦**（所有闸门均与会话无关），因此不阻塞 P1 的设计与实现，只影响协议层接法。

### 15.2 已知风险

| 风险 | 说明 | 处理 |
|---|---|---|
| pending 申诉死锁 | 出表/重算会因 pending 申诉硬阻断，而申诉页面已从代码删除、无界面可清 | 本期不解决（用户决定绕开）；**上线前查一次线上 pending 申诉计数**；命中时返回 `PENDING_APPEALS` 并提示联系维护人员 |
| 重算的部分不可逆 | 快照只覆盖正式表；`clean_status`、`person_daily_stats`、`month_perf_records`、`payroll_period_rows` 不在其中 | 明确表述为"正式表可回灌 + 全链路留痕"（§4.3 B）；源守卫 + 预演前置 + 确认语三层降低触发概率 |
| 未 warm 导致写错钱 | MCP 路径若漏调 `warm_config`，会用 env 默认参数重算工资 | §4.6 强制 + §13 冷缓存测试 + §7 闸门 7 |
| 写操作超时/重试 | 重算耗时长，客户端超时重试会重复执行 | 幂等（服务层保证）+ `timeout` 300s + `INTERNAL_WRITE` 禁止自动重试 + 审计可观测重复调用 |
| 线上演示被改写 | 演示期若误开写工具 | 只发 `read` Token + `disabledTools` 默认关闭 + 迁移预置 8 月封账 |

### 15.3 未验证假设与验证方式

| 假设 | 风险 | 验证方式 |
|---|---|---|
| WorkBuddy 桌面端能连本机 `127.0.0.1` 的 MCP 端点 | 若客户端把 MCP 流量走云端网关，本地 spike 不成立 | **P0 spike**（不通则临时公网映射） |
| 远端 MCP 挂载与握手细节（尾斜杠、`Accept` 头、会话初始化） | 挂载方式不对会静默失败 | P0 spike + 官方 client SDK 端到端测试 |
| SDK 是否提供无状态 HTTP 模式 | 决定 D2 选型 | P0 spike 查证 |
| 静态 Token 注入在桌面/移动/云端各端一致 | 移动端可能不支持自定义 header | P1 完成后桌面与移动端各验一次 |

**本机已确证的事实**（无需再验）：配置支持 `streamableHttp` + `headers` 的 `${VAR}` 插值；支持 `disabledTools`；连接器包为 `mcp.json` + `token-schema.json` + `skills/`；自定义连接器入口在「专家·技能·连接器 → 连接器 → 自定义连接器」；OAuth 为官方一等公民（官方索引含 `token_type: "mcp-oauth"`）。

## 16. 明确不做（YAGNI）

- 员工自助入口与 staff Token（管理端聚合数据本质是管理数据）
- 申诉相关能力（用户决定绕开；其页面已从代码删除）
- 文件上传通道（不做 base64 上传、不做一次性上传链接）
- 本系统侧调度器（交给 WorkBuddy 自动化）
- `delete` / `rerun` / `reset-all` / 密码重置 / 直接写 `AdjustRecord` / 快照回灌（一律 C 档）
- 限流（P1 不做，理由见 §7.6）
- 快照范围扩展到 raw/日统计/发薪表（本期只留正式表证据）
- OAuth 与定时推送（拆为独立 spec）
- 官方连接器市场上架（先做自定义连接器）
