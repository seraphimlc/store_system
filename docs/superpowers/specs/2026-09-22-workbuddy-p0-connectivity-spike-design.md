# WorkBuddy × 巡店结算系统 · P0 连通性验证设计（v2）

- 日期：2026-09-22
- 分支：`feat/workbuddy-integration`
- 状态：设计已获用户确认；**P0 Chunk 1–3 已实现并提交**（`745c752 → 7a298e4`，`mcp_service/tests` **47 passed**，含真实进程 e2e）；Chunk 4（人工验收 + 证据文档 + 索引/AGENTS）待执行
- 上游设计：`docs/superpowers/specs/2026-09-21-workbuddy-mcp-integration-design.md`（v3，**零实现**）
- 定位：本文**只覆盖 P0**。能力层全量、写工具、审计表、封账属 P1/P2。

## v2 修订说明

v1 的两处事实错误与八处设计缺陷由独立评审指出，已逐条复核并修正。主要变更：

1. **架构前提重写**：原设计假设"自定义连接器 + 本机 HTTP 探路"。经查证生态实况（§2.3），确定为**服务端远端 MCP + 市场可分发包**。
2. **删除多通道凭据 hack**（原 v1 §4.3 的 Bearer/`X-Visit-Token`/`?token=` 三通道）：`?token=` 会把凭据写进 URL 与日志，且与确认后的交付形态（header 注入）不符。改为**仅 Bearer 单通道**。
3. **修正 F9 过度声称**：本机唯一的自定义连接器是 **stdio**（§2.2 E6），v1 据此声称"自定义连接器可行"却不区分传输，属过度声称。
4. **修正 W1 回退机制**：v1 说自定义连接器注册在 `connectors/<uuid>/mcp.json` 且卡点是"加密的 enabled 列表"，**两者皆错**（§2.2 E7、E8）。
5. **修正 F15 表述**：v1 称"无 Token 机制"，实为"无面向外部客户端的 API Token（Bearer）机制"。
6. **补全 `visit_month_summary` 聚合口径**（§5.4）——v1 未定义，实现者可合法返回 `persons: 54`。
7. **统一验收基准语义**（§7）——v1 在 §2.1/§5/§7 三处自相矛盾。
8. **补 §12.2 与 v3 §14 的冲突记录**，并收录"`stateless_http` 已确认存在"（D2(a) 近乎零成本得解）。

---

## 1. 已确认决策

### 1.1 用户定义的三条原则（2026-09-22）

| # | 原则 | 对设计的约束 |
|---|---|---|
| P1 | **现有 web 系统相关的能力和接口不能被影响** | 只做增量；不改既有路由行为/返回语义/数据模型既有列。"网页可下掉"是**未来业务决定**，不等于现在可动接口 |
| P2 | **走正确标准的 WorkBuddy 接入方式**；后续还有很多系统接入，本系统只是第一个业务场景 | 不做一次性 trick；产物须拆出**可复用范式**（包结构、命名、鉴权、SKILL 写法、验收清单）；官方文档/后台口径优先于 reverse-engineering |
| P3 | 系统**部署在客户服务器、走公网访问**；测试环境都在本机 | 端点公网可达（需 TLS）；本机是测试环境；内网穿透/反向隧道**不是**本客户的议题 |

**P3 修正了 v1 的推论**：v1 把"必须跑在客户自己的环境里"推论成"内网隔离、公网不可达"，并据此把 P0 定位为"私有化可达性验证"。正确理解是**专属实例 + 数据归属客户 + 公网可达**。

### 1.2 用户确认的四个形态决策

| 项 | 决策 | 依据 |
|---|---|---|
| 接入形态 | **官方/市场连接器（可分发包）** | 用户选择；生态有完整先例（§2.3） |
| 连接器形态 | **远端 streamableHttp，MCP 随系统部署** | 用户选择；官方 bundle 有 `${VAR}` 插值 URL 先例（§2.3 X9） |
| 实现边界 | **独立服务：同仓库、不同进程、独立依赖** | 用户选择；复用 `app/services/*`，现有 app/venv/镜像零影响 |
| 鉴权 | **静态 Token（`auth_mode: token`）** | 用户选择；生态标准做法（§2.3 X4/X5） |

---

## 2. 事实基础

**证据等级**：`实测` = 本机命令输出；`反编译` = 客户端 `app.asar` 提取；`外部` = npm/公开资料。**推论一律标注为推论**，不混入事实表。

### 2.1 仓库与运行环境（实测）

| # | 事实 | 证据 |
|---|---|---|
| F1 | 现有 **66 个路由**，**唯一返回 JSON 的端点是 `GET /healthz`** | 全路由清点 |
| F2 | 无面向外部客户端的 **API Token（Bearer）机制**；`app/auth.py` 有签名会话 Cookie（itsdangerous + TTL）与 CSRF | `app/auth.py` |
| F3 | 无 `app/api/`、无 `app/mcp/`、`requirements-web.txt` 无 `mcp` 依赖、无相关迁移 | 目录与依赖清单 |
| F4 | 生产镜像 `python:3.11-slim`；`deploy/entrypoint.sh:5` 为 `uvicorn --workers 2` | `deploy/Dockerfile`、`deploy/entrypoint.sh` |
| F5 | 本地项目 venv 为 **Python 3.9.6** | `./.venv/bin/python -V` |
| F6 | `mcp` 官方 Python SDK **全部版本（1.0–2.2.0）要求 Python ≥ 3.10** | PyPI JSON API 逐版本 `requires_python` |
| F7 | 本机有 Homebrew `python3.12` / `python3.13`；`node v25.2.1` | `command -v` |
| F8 | `mcp==2.2.0` 在**全新 python3.12 venv 安装成功**（exit 0） | 实测安装 |
| F9 | mcp 2.x **删除了 `mcp.server.fastmcp`**：`FastMCP` 改名 `MCPServer`（`from mcp.server.mcpserver import MCPServer`），旧路径主动抛 `ModuleNotFoundError` | 读已安装 SDK 源码 |
| F10 | `MCPServer.run(transport="streamable-http", host=, port=, streamable_http_path=, json_response=, **stateless_http=**, session_idle_timeout=, max_sessions=, transport_security=)` | 读已安装 SDK 源码 |
| F11 | `TransportSecuritySettings(enable_dns_rebinding_protection: bool = True, allowed_hosts: list, allowed_origins: list)`；`allowed_hosts` 支持 `"127.0.0.1:*"` 通配端口；`_validate_origin` 对**缺失 Origin 放行**。**两处易踩坑**：① 完全不传 settings 时 `TransportSecurityMiddleware` 构造的是 `enable_dns_rebinding_protection=False`（默认关闭）② 开启保护但 `allowed_hosts=[]` 时 `_validate_host` 拒绝一切请求。§5.6 的显式配置避开两者 | 读已安装 SDK 源码 |
| F12 | 协议版本表：`KNOWN = 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25, 2026-07-28`；`HANDSHAKE = …2025-11-25`；`MODERN = 2026-07-28`（"无状态 per-request 信封"时代）；`LATEST = 2026-07-28` | `mcp_types/version.py` |

### 2.2 WorkBuddy 客户端（反编译 + 实测）

| # | 事实 | 证据 |
|---|---|---|
| E1 | 客户端内嵌**官方 MCP TypeScript SDK**（`client/index.js`、`streamableHttp.js`、`sse.js`、`stdio.js`） | `app.asar` |
| E2 | 客户端支持的协议版本含 `2025-11-25`/`2025-06-18`/`2025-03-26`/`2024-11-05`；**客户端**在未带 `mcp-protocol-version` 头时的兜底版本为 `2025-03-26`（取自客户端内嵌 SDK，**非服务端行为**） | `app.asar` |
| E3 | 市场连接器配置为 `mcp.json` 的 `mcpServers`；支持 `streamableHttp`/`streamable-http`/`sse`/`stdio`/`http`；支持 `${VAR}` 插值、`headers`、`staticHeaders`、`timeout`、`disabledTools`、`staticEnv` | `~/.workbuddy/connectors/*/mcp.json` |
| E4 | 市场共 **270** 个连接器包，格式 = `mcp.json` + `skills/<名>/SKILL.md` + `references/` | `~/.workbuddy/connectors-marketplace/` |
| E5 | 存在明文 HTTP + 裸公网 IP 的官方连接器（`http://47.114.32.85:9010/mcp`），说明客户端直连、不强制 HTTPS | 同上 |
| E6 | **自定义连接器仅有 stdio 先例**：`~/.workbuddy/mcp.json` → `{"playwright": {"command": "npx", "args": ["@playwright/mcp@latest"], "disabled": false}}`；运行日志 `stdio MCP custom-mcp:playwright transport: command=npx`。**没有任何 HTTP 传输的自定义连接器被执行过** | `~/.workbuddy/mcp.json`、`~/.workbuddy/logs/` |
| E7 | 自定义连接器注册表 = **`~/.workbuddy/mcp.json`**（明文），审批门 = **`~/.workbuddy/mcp-approvals.json`**（键 `sha256::<名字>` → 时间戳）。市场连接器则在 `connectors/<uuid>/mcp.json`，两处互不相同 | 实测读取 |
| E8 | `connector-states.v3.json` 是**明文**（`"enabled": []` 可直接读），**只有凭据加密**（aes-256-gcm + hkdf，`.master.key`） | 实测读取 |
| E9 | 企业自定义连接器策略从后台拉取（`EnterpriseCustomConnectorPolicy`，1.5s 超时、stale-cache、fail-open）；本机 `.policy-cache.json` 记录该企业账号 `{"allowed": true, "policy_mode": "all"}` | `app.asar` + `~/.workbuddy/.policy-cache.json` |
| E10 | 连接器状态含 `headerOverridesBearerStripped: true`、`mcpSecurityMigrated: true` —— 近期做过"剥离 header 覆盖中 Bearer"的安全迁移 | `connector-states.v3.json` |
| E11 | 客户端存在沙箱概念（`settings.json` 的 `sandbox.extraAllowWrite` 白名单） | `~/.workbuddy/settings.json` |

### 2.3 生态与官方流程（外部）

| # | 事实 | 证据 |
|---|---|---|
| X1 | **官方开放平台 `open.workbuddy.cn` 可达（HTTP 200）** | 实测 |
| X2 | 第三方连接器的**标准打包结构**：`connector-meta.json`（含 `auth_mode`）+ `token-schema.json`（用户自填凭证表单）+ `mcp.json` + `icon.svg` + `skills/<名>/SKILL.md` | npm `workbuddy-postgres-connector` README |
| X3 | **发布流程**：注册认证 open.workbuddy.cn → 后台创建「连接器」→ 上传 zip（`connector-meta.json` 须在 **zip 根层**）→ 测试环境调试 → 提交审核 → 上架市场 | 同上 |
| X4 | `auth_mode: token` 的机制：用户点「连接」→ 客户端弹出 `token-schema.json` 描述的表单 → **凭据仅存本机 `~/.workbuddy`，不经过云端** → 连接时以 `${VAR}` 注入 | 同上 |
| X5 | `auth_mode: token` **需 WorkBuddy ≥ 4.23.0**；`password` 类型字段满足审核安全红线（对话与日志中不出现明文密码） | 同上 |
| X6 | **官方本地调试路径**：客户端「新建连接器 → 选择**本地目录**」，`mcp.json` 的 `command` 改 `node`、`args` 改绝对路径，env 照填——**无需先发布 npm、无需先上架** | 同上 |
| X7 | 同构案例 `baihua-mes-mcp`：客户自建 MES 私有系统经 stdio 连接器开放给 WorkBuddy；MCP 工具只是**代理调用后端 HTTP 接口**（`/api/mcp/domains`、`/api/mcp/query`、`/api/mcp/aggregate`） | npm README |
| X8 | 部署建议：给客户建**专用查询账号**（避免与网页端登录互踢 + 最小权限） | 同上 |
| X9 | 官方 bundle 中存在 **`url` 用 `${VAR}` 插值**的连接器：`connector:tdengine` → `url: "${TDENGINE_API_SCHEMA}://${TDENGINE_API_HOST}:${TDENGINE_API_PORT}/api/v1/mcp/stream"` | E3 同批文件 |

### 2.4 本地库基线（实测，只读 SQL）

| 月 | 行数 | 总点数 | 1点 | 2点 | 人数 | `AGENTS.md` 记 |
|---|---|---|---|---|---|---|
| 2026-08 | 12507 | 16787 | 8227 | 4280 | 34 | 行数12511 / 点16791 / 1点8231 / 2点4280 / 人34 |
| 2026-09 | 15067 | 19471 | 10663 | 4404 | 34 | 行数— / 点19572 / 1点10728 / 2点4422 / 店15150（**列义不同，勿逐格比对**） |

与 `AGENTS.md` 的偏差是既存事实（对应 v3 §15.1 未决决策 **D1**）。**本设计不解决 D1**，验收语义见 §7。

---

## 3. 边界

### 3.1 做

- MCP 服务端最小实现（**独立进程**、目录 `mcp_service/`、依赖文件 `requirements-mcp.txt`）。
- 两个只读工具 + 单一 Bearer 鉴权 + 请求级证据日志。
- 自动化测试 `mcp_service/tests/`（T1–T4 + 连接器包测试；**47 passed** 已实现）。
- **连接器包骨架** `deploy/connector/visit-settle/`（`mcp.json` / `token-schema.json` / `connector-meta.json` / `icon.svg` / `skills/visit-settle/SKILL.md`）。
- P0 实测记录 `docs/workbuddy-p0-验证记录.md`。
- `.gitignore` 增补（`mcp_service/logs/`；`mcp_service/.venv/` 由既有 `.venv/` 规则覆盖）——已提交（745c752）。
- 依仓库强制约定更新 `docs/索引.md` 与 `AGENTS.md`（v3 §14 亦要求每期完成后更新）。

### 3.2 不做

- **不改** `app/` 下任何既有文件、不改既有路由行为与返回语义、不动 `migrations/`、**不动项目 `.venv`（3.9.6）**、不改 `requirements-web.txt`、不改 `deploy/Dockerfile`。
- **不碰** 线上 `store-prod`、不做任何发布。
- **不做写操作**：数据连接以只读方式打开（§5.3），结构上不可写。
- **不实现** `api_tokens` 表、`mcp_audit_log`、`sealed_months`、封账、预演、确认语、写工具（P1/P2）。
- **不实现**上传通道、员工自助、调度器（v3 §16 的 YAGNI 清单继续有效）。
- **不重写**任何业务逻辑；只用已结算的 `formal_records`，**不重新推导判重/锚点规则**。

---

## 4. 终局架构与 P0 的位置

```
客户服务器（公网 HTTPS，如 https://<customer-domain>）
  nginx ──/──────────► FastAPI 现有应用（app/，--workers 2，不受影响）
        └─/mcp────────► MCP 服务（mcp_service/，--workers 1，独立进程）
                            └── import app.services.* / app.models（业务逻辑唯一一份）
                                        │
                                     DB（SQLite 本地 / PG 线上）

WorkBuddy 桌面端（用户机）
  └─ 连接器包（市场分发）：mcp.json(url=${VISIT_BASE_URL}/mcp, Authorization: Bearer ${VISIT_TOKEN})
     + token-schema.json（地址 + Token，凭据只存本机）
```

P0 只实现图上 **`/mcp` 那条边 + 连接器包骨架**，且只读。

---

## 5. P0 服务端设计

### 5.1 运行时

| 项 | 选择 |
|---|---|
| 目录 | `mcp_service/`（同仓库、独立进程；与 `app/` 平级，边界一眼可见） |
| 本地解释器 | Homebrew `python3.12` + `mcp_service/.venv/`（gitignore） |
| 生产解释器 | 复用 `python:3.11-slim` 基础镜像（≥3.10 即满足 F6），**独立容器**，`--workers 1` |
| 依赖 | `requirements-mcp.txt` = 应用依赖（以便 import `app.*`）+ `mcp==2.2.0`（F8 已实测） |
| SDK | `mcp==2.2.0`，用 **`MCPServer`**（F9：`FastMCP` 在 2.x 已移除，v1 教程代码不可用） |
| 传输 | streamable HTTP，路径 `/mcp`；本地 `127.0.0.1:8765`；生产由 nginx 反代 |
| 无状态 | P0 用默认（有状态）。`stateless_http=True` 作为 P1 多实例/多 worker 的既定手段（F10） |

### 5.2 鉴权（单通道）

- 仅接受 `Authorization: Bearer <token>`；`hmac.compare_digest` 比较。
- 缺失/不匹配 → **401** + `WWW-Authenticate: Bearer`，响应体为 §5.5 错误信封。
- **启动即失败**：`VISIT_MCP_TOKEN` 未设置或为空 → 立刻退出非零（防止空配置静默通过，使 A3 失效）。
- Token 来源：P0 用环境变量；P1 迁移到 `api_tokens` 表（v3 §6.1）。
- **接缝设计**：能力函数签名为 `f(db, actor, **params)`，鉴权模块只负责"解析凭据 → `Actor`"。换 OAuth 时只替换该模块（v3 §6.2 保持不变）。

### 5.3 数据访问

- 复用 `app.db` / `app.models`（SQLAlchemy）；**不做字符串拼接**，参数化 SQL（§5.4 的绑定参数 SELECT）允许。
- 本地以只读 URI 打开：`DATABASE_URL="sqlite:///file:/<abs>/store_settle_live.db?mode=ro&uri=true"` —— 保留 v1 的"结构上不可写"保证，同时走 ORM。
- 生产 `DATABASE_URL` 指向 PG；本服务账号一律只读权限。

### 5.4 工具契约

统一信封（对齐 v3 §8）：

```
成功: {ok: true,  data: {...}}
失败: {ok: false, error: {code, message, hint}}
```
信封同时作为 **MCP `structuredContent`** 返回，并附等价的序列化文本（P0 要观测客户端究竟 surface 哪一路——这是一条真实验收观察项，不是实现细节）。

**工具 1 · `visit_ping`**（诊断用，P1 删除）

```
visit_ping() -> {ok:true, data:{
  server: "visit-settle-mcp",
  sdk_version: "2.2.0",
  protocol_version: "<本次协商结果>",
  client_info: "<客户端上报的名称/版本>",
  auth_header_seen: true|false,      # 是否收到 Authorization 头（不记值）
  session_id_seen: true|false,       # 请求是否带 Mcp-Session-Id
  now: "<ISO8601>"
}}
```
中文描述：「诊断用：回显服务端身份、协商到的协议版本、以及本次调用是否携带了 Authorization 凭据头。用于排查 WorkBuddy 连接与鉴权问题。」

`auth_header_seen` 是 **E10（Bearer 可能被剥离）** 的可观测化手段——把猜测变成一条日志事实。

**工具 2 · `visit_month_summary`**（唯一真实数据工具）

**聚合口径必须写死**（v1 未定义，实现者可合法返回 `persons: 54`）：

> 数据来源 = 已结算的 **`formal_records`**。**不得**读 `persons` 表（54 行为全量人员，非本月），**不得**重新推导判重/锚点规则（那是 `flow.judge_import` 的职责，另行调用会与正式表口径分叉）。
> 月份过滤用**范围比较**（跨 SQLite/PG 方言安全），不用 `substr`/`to_char`。

```sql
SELECT COUNT(*)                                    AS formal_rows,
       SUM(points)                                 AS points_total,
       SUM(CASE WHEN points = 1 THEN 1 ELSE 0 END)  AS p1_count,
       SUM(CASE WHEN points = 2 THEN 1 ELSE 0 END)  AS p2_count,
       COUNT(DISTINCT person_code)                  AS persons
FROM formal_records
WHERE japan_date >= :month_start AND japan_date < :next_month_start;
```
（`:month_start` = `YYYY-MM-01`，`:next_month_start` = 次月 01。实测该写法与 `substr` 写法结果一致：2026-09 = 15067/19471/10663/4404/34。）

```
visit_month_summary(month: str) -> {ok:true, data:{
  month, formal_rows, points_total, p1_count, p2_count, persons
}}
```
- `month` 用 JSON Schema `pattern: ^[0-9]{4}-(0[1-9]|1[0-2])$`；非法 → `BAD_MONTH`。**不用 `\d`**（v3 §7.2：`\d` 放行全角「２０２６-08」，会静默返回 0 行）。
- 该月无数据 → **`ok:true` 且各计数为 0**，附 `hint` 说明。无数据是合法查询结果，不是失败。
- 中文描述：「查询某结算月（格式 YYYY-MM）正式表的行数、总点数、1点/2点条数与人数。数据来自已结算的正式表，只读。」

### 5.5 错误码（P0 只用三个）

| code | 触发 | hint |
|---|---|---|
| `UNAUTHORIZED` | 无/错 Bearer | "请在 WorkBuddy 连接器设置中重新填写 Access Token" |
| `BAD_MONTH` | 月份格式非法 | "月份必须是 YYYY-MM，例如 2026-09" |
| `INTERNAL` | 未预期异常（含 DB 打不开） | "系统内部错误，已记录；可重试"（只读工具，故非 `INTERNAL_WRITE`） |

异常必须被捕获并转成信封，不得穿透到协议层（v3 §8.2）。

### 5.6 传输安全与日志

**Host/Origin（采纳"保留保护"而非 v1 的"关闭保护"）**

SDK 行为（F11）：传入 settings 时 `enable_dns_rebinding_protection` 默认 `True`；`allowed_hosts` 支持 `"127.0.0.1:*"` 通配端口；**缺失 Origin 放行**。

```python
TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*", "<生产域名>:*"],
    allowed_origins=[],          # 缺失即放行；若客户端发来意外 Origin，日志会暴露
)
```
**不关闭保护**（v1 的"禁用"既无必要、又更弱）。若实现中确认必须放宽，须登记为"P1 必须恢复"并写明理由。
`Host`/`Origin` 原始值必须进日志：**意外 Origin 是本地连通的典型静默失败源**。

**日志（`mcp_service/logs/requests.jsonl`）**：每请求一行
`ts`、`method`、`path`（**查询串必须剥离/脱敏后记录**）、`header_names`（只记名字）、`auth_header_seen`、`host`、`origin`、`protocol_version`、`session_id_seen`、`status`、`duration_ms`、`body_digest`。
**凭据值、查询串、请求体原文一律不落日志**（本设计不再保留任何凭据通道，无例外）。

---

## 6. 连接器包骨架（`deploy/connector/visit-settle/`）

按 X2/X3 的结构，P0 只填必需项：

| 文件 | P0 内容要点 |
|---|---|
| `connector-meta.json` | 名称/描述/图标/`auth_mode: "token"` |
| `token-schema.json` | 两个字段：`VISIT_BASE_URL`（文本，如 `https://<customer-domain>`）、`VISIT_TOKEN`（`password` 类型） |
| `mcp.json` | `{"type":"streamableHttp","url":"${VISIT_BASE_URL}/mcp","headers":{"Authorization":"Bearer ${VISIT_TOKEN}"},"timeout":300000}` |
| `icon.svg` | 占位图标 |
| `skills/visit-settle/SKILL.md` | 业务口径：月份必须 `YYYY-MM`；点数口径 1点/2点；先查后写；**P0 阶段本连接器只读** |

`timeout` 取 300000ms（**P1 生效的预设**：P0 只读工具用不到长超时；P1 的 `rebuild_month` 需重判整月并写上万行，60s 会超时并诱发重试，v3 §11）。SKILL.md 的"先查后写"同为 P1 写工具开通前的预备口径。

**待官方确认的唯一可能翻案项（§11 U1）**：第三方提交的连接器是否允许 **用户自填 URL**（`${VISIT_BASE_URL}`）。X9 的先例来自官方 bundle；若审核不允许，退路见 W1。

---

## 7. 验收标准

### 7.1 基准语义（统一，取代 v1 的三处矛盾）

- **通过判据（唯一）**：`visit_month_summary("2026-09")` 的五个字段 **== 同一时刻对同一份库执行 §5.4 SQL 的结果**。
- 库是可变对象（应用会写它；`AGENTS.md` 记录了月度重算），因此**冻结数字不作为判据**。
- 冻结数字仅作**证据留档**：验证时记录 `(SQL 输出, 数据库文件 sha256)` 到实测记录，用于日后复现讨论。
- §2.4 的数字是**设计时的观测**，不是判据。

### 7.2 验收清单（全部客观可判）

| # | 标准 | 判定 | 通过条件 |
|---|---|---|---|
| A1 | 客户端能连上并列出工具 | 客户端界面 | 可见 2 个工具且中文描述非空 |
| A2 | 真实数据正确 | 脚本比对 | 五字段逐一 == 同刻 SQL 结果（§7.1） |
| A3 | 错误凭据被拒 | **两侧同时** | 客户端可见拒绝 **且** 服务端日志有对应 401 |
| A4 | 凭据头透传 | `visit_ping` 回显 + 日志 | `auth_header_seen == true`（若为 false 即命中 E10 风险 W2） |
| A5 | 握手事实留档 | `docs/workbuddy-p0-验证记录.md`（§3.1 的证据载体） | 逐项回答：①`initialize` 是否成功 ②协商到的 `protocolVersion` ③请求是否带 `Mcp-Session-Id` ④服务端是否有状态即可工作 ⑤**尾斜杠与 `Accept` 头**的实际值 ⑥客户端 surface 的是 `structuredContent` 还是文本 |
| A6 | 会话结论（D2） | `docs/workbuddy-p0-验证记录.md` | 明确写出：v3 §7.6 的 a/b/c 三条中，本 P0 **能**判定哪条、**不能**判定哪条及原因（P0 为单进程，故"多 worker 会话"整体不可判；但 a 已被 F10 证有） |

A3 要求"两侧同时"是刻意的：只看客户端报错无法区分鉴权失败与协议失败。A5/A6 把 v1 那句不可判的"记录真实表现"换成逐项问答。

---

## 8. 测试

T1–T4 不依赖 WorkBuddy，可无人跑完；T5 需要用户。

| # | 文件 | 依赖 | 断言 |
|---|---|---|---|
| T1 | `mcp_service/tests/test_visit_ping.py` + `test_month_summary.py` | `pytest`、`mcp`（官方 **Python client SDK**）、临时 SQLite | 工具清单 = 2 且描述非空；`visit_month_summary` 五字段 == 同刻 SQL；非法月份 → `BAD_MONTH`；空月 → `ok:true` 且计数 0 |
| T2 | `mcp_service/tests/test_auth.py` | 同上 | 无 Bearer → 401 + `WWW-Authenticate`；错 Bearer → 401；对 Bearer → 握手成功；**空配置启动 → 非零退出** |
| T3 | `mcp_service/tests/test_readonly.py` | 同上 | 对连接执行写语句 → 数据库报只读错误（证明"结构上不可写"） |
| T4 | `mcp_service/tests/test_reqlog.py` | 同上 | 日志行含 `host`/`origin`/`auth_header_seen`/`body_digest`；**不含** Authorization 值、查询串、请求体原文 |
| T5 | 人工 | WorkBuddy 桌面端 + 连接器包 | 满足 A1–A6（A5/A6 依赖本次人工运行） |
| T6 | `mcp_service/tests/test_connector_package.py` | `pytest` | 包结构完整；`auth_mode=token`；mcp.json 每个 `${VAR}` 都由 token-schema 声明（漏声明 = 客户端解析不出地址）；token 字段为 password 类型 |

**开发期进展门（不需要 WorkBuddy）**：T1–T4 全绿 + 官方 client SDK 端到端跑通，构成"服务端是对的"的证据；此后 T5 失败才可归因到客户端侧。

---

## 9. 风险与退路（每条退路均为可执行动作）

| # | 风险 | 探测 | 退路（可执行） |
|---|---|---|---|
| W1 | **市场审核不允许用户自填 URL**（§6 唯一可能翻案项） | open.workbuddy.cn 后台/官方文档 | ① 改固定域名包（每客户一个包）② 退化成本地 stdio 包（X6/X7 范式）：包用 `node` 调同一套后端 HTTP API。**需在 P1 前定，否则包结构返工** |
| W2 | Bearer 被剥离（E10） | `visit_ping.auth_header_seen` + 日志 | 服务端并行接受另一个自定义 header 名（如 `X-Visit-Token`），连接器侧改一处配置；架构不变。**仅诊断期例外、须登记恢复**（对 §5.2 单通道的临时放宽） |
| W3 | 客户端不认本机 `127.0.0.1` 端点 | T5 | ① 绑 `0.0.0.0` + 局域网 IP，**同时**保持 `enable_dns_rebinding_protection=True` 并把该 IP 加进 `allowed_hosts`（不得因放宽而关保护）② 本机测试**临时**用 stdio bridge（`npx mcp-remote <url>`）桥接：客户端对 stdio 自定义连接器有确证先例（E6）。**注意**：bridge 路径只能验 A1/A2，**不能**验 A4（凭据注入方式不同），须在记录中标注该局限 |
| W4 | 协议版本不匹配 | 日志 `protocol_version` | 客户端上限 `2025-11-25`，服务端握手路径上限同为 `2025-11-25`（F12），默认应匹配；若不合，显式把协商版本固定到 `2025-11-25`，或降到 `mcp<2` 并记录理由。**不升到 `2026-07-28`**（客户端不支持该 "modern" 时代） |
| W5 | 端口占用 | 启动时 | 换 `VISIT_MCP_PORT`；启动日志打印实际监听地址 |
| W6 | 客户端能连但工具不可见 | 日志有 `tools/list` 而界面无 | 把 `tools/list` 原始响应体写入日志（临时提高日志级别），对照 E2 的协议版本确认 schema 形态；仍不通则用 stdio bridge 二分（服务端 vs 客户端） |

---

## 10. 人工步骤（不可替代）

| 步 | 动作 | 卡点 |
|---|---|---|
| 0 | 客户端「新建连接器」→ 选择**本地目录**（X6）：`command=python3.12`、`args=["<abs>/mcp_service/server.py"]`，填 env（`VISIT_MCP_TOKEN` 等） | 前置探测。E9 显示企业策略 `allowed:true`，预期不阻塞 |
| 1 | 让 agent 调用两个工具，把**界面原文**（成功或报错）反馈 | — |
| 2 | 在 `open.workbuddy.cn` 确认 §11 U1（用户自填 URL 是否允许），**并核对 X2/X3/X6**（包结构、审核流程、本地目录调试路径目前均为单源 npm README，未获官方佐证）；结论写入证据文档 | **影响 W1，建议尽早** |

步 0 走"本地目录"而非手改 `~/.workbuddy/mcp.json`：后者需匹配 `mcp-approvals.json` 的 `sha256::名` 审批键（E7），属绕过客户端安全门，不做。

---

## 11. 未验证假设

| # | 假设 | 风险 | 验证方式 |
|---|---|---|---|
| U1 | **第三方连接器允许用户自填 URL**（`${VISIT_BASE_URL}`） | 若不允许，包结构与分发方式返工（W1） | open.workbuddy.cn 后台/官方文档（人工步 2） |
| U2 | 客户端能连本机/内网 HTTP MCP 端点 | 若客户端把 MCP 流量走云端网关，则本机测试不成立 | T5 |
| U3 | 客户端会原样转发连接器模板注入的 `Authorization` 头 | E10 显示曾收紧过 Bearer | A4 |
| U4 | 市场包的工具发现不依赖额外清单文件 | 若必须成包才能发现工具，本地目录调试路径受限 | T5 + X6 对照 |
| U5 | 本机单进程结论可外推到生产多实例部署 | 生产为独立容器 `--workers 1`，但多副本仍需 `stateless_http` | P1 用 F10 的无状态开关验证 |
| U6 | 连接器包结构（X2）、发布审核流程（X3）、本地目录调试路径（X6）与官方口径一致 | 三条均出自同一篇第三方 npm README，未获官方佐证；包骨架与 Task 8 依赖它们 | open.workbuddy.cn 后台/文档（人工步 2） |

**U1 是当前唯一可能推翻形态的未知**，其余都在 P0 内可证。

---

## 12. 与 v3 spec 的关系

### 12.1 必然修订项（P1 执行，本轮只记录）

| # | v3 原文 | 为何要改 |
|---|---|---|
| 1 | §2「上传留在网页」（理由"远端服务拿不到本地 Excel"） | 用户 P1 原则下"网页可下掉"须有 MCP 侧等价物；且该理由本身不成立——远端 MCP 与文件是否本地无关 |
| 2 | §2/§3「远端 MCP 挂在公网 `store-prod`」 | P3 已澄清方向正确（专属实例 + 公网），但需补"随系统部署到客户服务器 + nginx `/mcp`"的落地口径 |
| 3 | §3「同进程挂载」 | 用户决策改为**独立进程/容器**（`mcp_service/`）；v3 §7.6 的 lifespan 与 `--workers 2` 会话议题随之消解 |
| 4 | §7.6 决策 D2 | `stateless_http` 已确认存在（F10），选项 a 成立；协议层 `2026-07-28` "modern" 时代对 WorkBuddy **不可达**（E2 上限 `2025-11-25`） |

### 12.2 与 v3 §14 的验收冲突（v1 遗漏，此处登记）

v3 §14 把「查证 SDK 无状态能力」与「给出 D2 定论并回写 §7.6」列为 **P0** 验收项 ⑤。本 P0：

- 「SDK 无状态能力」**已答**（F10：`stateless_http` 存在）——不再推迟；
- 「D2 定论」**部分可答**：选项 a 成立；多 worker 会话因 P0 为单进程**不可判**，按 A6 明确列出"不可判"，留 P1 用 U5 补。

**两文档对 P0 出口的要求以本节为准**，避免后续规划者读到两套 P0。

### 12.3 本文档交付物归属

§3.1 的 `docs/workbuddy-p0-验证记录.md` 是**验收证据**；§12.1 的修订清单是**对 v3 的输入**，两者不重复：修订清单在 P1 开始时直接落到 v3 的新版本里。

---

## 13. 明确不做（YAGNI）

- 不做写工具、不做上传通道、不做审计表落库、不做封账/预演/确认语。
- 不做 OAuth（用户已选静态 Token；仅保留 `凭据 → Actor` 接缝）。
- 不把 MCP 挂进 FastAPI 进程。
- 不用 Node/TS 重写服务端（交付形态为远端服务端 MCP，Python 可直连复用服务层）。
- 不做连接器市场的正式提交（P0 只产出包骨架；提交与审核属后续阶段）。
- 不解决 D1（基准数字）。
