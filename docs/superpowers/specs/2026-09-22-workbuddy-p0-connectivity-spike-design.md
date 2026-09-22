# WorkBuddy × 巡店结算系统 · P0 连通性验证设计（Connectivity Spike）

- 日期：2026-09-22
- 分支：`feat/workbuddy-integration`
- 状态：设计已获用户确认，待评审
- 上游设计：`docs/superpowers/specs/2026-09-21-workbuddy-mcp-integration-design.md`（v3，**零实现**）
- 定位：本文**只覆盖 P0（连通性与鉴权透传验证）**。能力层、Token 表、审计、封账等属 P1/P2，不在本文范围。

---

## 1. 背景与目标

### 1.1 用户意图（本轮新增，v3 spec 未包含）

1. 巡店结算系统不再以网页为主要交付形态；**WorkBuddy 里的 agent/skill 是用户的操作界面**，MCP 是它伸进本系统的入口。
2. 本系统是**私有化系统**：数据不出客户边界，**必须跑在客户自己的环境里**（用户 2026-09-22 明确选择）。
3. 实际使用者用 **WorkBuddy 桌面客户端，装在客户内网的机器上**（用户明确选择）。
4. 若整合效果与客户认可度成立，**网页形态可以下掉**——届时上传、身份、看板、导出等网页独占能力都必须在 MCP 侧有等价物。

### 1.2 本轮的单一目标

**用最小代价换取两个未知的确定性**：

- **U1**：WorkBuddy 桌面客户端能否连接**本机 `127.0.0.1` 的 streamable HTTP MCP 端点**。
- **U2**：客户端能否把凭据**透传**到该端点（`Authorization: Bearer` 是否被保留）。

选 `127.0.0.1` 是因为它是"不可从公网到达"的**最强形式**：它通过则私网/内网端点基本可通过；它不通过则私有化路线需要换玩法（反向隧道或客户端侧 bridge）。这是一次**证伪优先**的探测，不是产品实现。

---

## 2. 已确证事实（含证据）

以下均为本机实测/查证所得，不是推测。

| # | 事实 | 证据来源 |
|---|---|---|
| F1 | `mcp` 官方 Python SDK **全部版本**（1.0 – 2.2.0）要求 **Python ≥ 3.10** | PyPI JSON API 逐版本 `requires_python` |
| F2 | 生产镜像为 `python:3.11-slim`，可运行 `mcp` SDK | `deploy/Dockerfile:1` |
| F3 | 本地项目 venv 为 **Python 3.9.6**，**无法安装 `mcp`** | `./.venv/bin/python -V` |
| F4 | 本机另有 Homebrew `python3.12` / `python3.13`；`node v25.2.1` / `npx 11.6.2` | `command -v` |
| F5 | 连接器配置为 `mcp.json` 的 `mcpServers` 表，支持 `streamableHttp`/`streamable-http`/`sse`/`stdio`/`http`，支持 `${VAR}` 插值、`headers`、`staticHeaders`、`timeout`、`disabledTools`、`staticEnv` | `~/.workbuddy/connectors/*/mcp.json` |
| F6 | 客户端内嵌**官方 MCP TypeScript SDK**（`client/index.js`、`client/streamableHttp.js`、`client/sse.js`、`client/stdio.js`） | `/Applications/WorkBuddy.app/Contents/Resources/app.asar` |
| F7 | 客户端支持协议版本 `2025-11-25`、`2025-06-18`、`2025-03-26`、`2024-11-05`、`2024-10-07`；SDK 服务端默认取 `2025-03-26` | asar 内 `SUPPORTED_PROTOCOL_VERSIONS` / `mcp-protocol-version` |
| F8 | 市场连接器包格式 = `mcp.json` + `skills/<名>/SKILL.md` + `references/`；市场共 **270 个**包 | `~/.workbuddy/connectors-marketplace/connectors/fbs-connector/` |
| F9 | **自定义 MCP 连接器可行且本机跑过** | `~/.workbuddy/logs/mcp-runtime/custom-mcp_playwright-5e9d46fa/` |
| F10 | **自定义连接器受企业管理后台策略管控**：从企业后台拉 `USER_CHECK_PATH` 策略判 `allowed`，1.5s 超时、stale-cache、**fail-open** | asar 内 `EnterpriseCustomConnectorPolicy` / `EnterpriseCustomConnectorPolicyError` |
| F11 | 客户端近期做过安全迁移：连接器状态里有 `headerOverridesBearerStripped: true`、`mcpSecurityMigrated: true` | `~/.workbuddy/connectors/*/connector-states.v3.json` |
| F12 | 凭据按账号加密存储（aes-256-gcm + hkdf），有 `headerOverrides` / `envOverrides` 注入位 | 同上 + `README-DO-NOT-DELETE.txt` |
| F13 | 客户端存在沙箱概念（agent 文件写入走 `extraAllowWrite` 白名单） | `~/.workbuddy/settings.json` |
| F14 | 市场里存在明文 HTTP + 裸公网 IP 的连接器（`http://47.114.32.85:9010/mcp`），说明客户端直连端点、不强制 HTTPS | F5 同一批 mcp.json |
| F15 | 本系统现有 **66 个路由**，**唯一返回 JSON 的端点是 `GET /healthz`**；无 Token 机制、无 `app/api/`、无 `app/mcp/`、无相关迁移 | 全路由清点（见 §9 附注） |
| F16 | `deploy/entrypoint.sh:5` 为 `uvicorn --workers 2` | `deploy/entrypoint.sh` |

### 2.1 本地库实测基线（只读 SQL）

验收基准**只能**是"与同一份库的 SQL 直查一致"，不能用 `AGENTS.md` 的数字——两者已经不一致：

| 月 | 行数 | 总点数 | 1点 | 2点 | 人数 | `AGENTS.md` 记为 |
|---|---|---|---|---|---|---|
| 2026-08 | 12507 | 16787 | 8227 | 4280 | 34 | 12511 / 16791 / 8231 / 4280 / 34 |
| 2026-09 | 15067 | 19471 | 10663 | 4404 | 34 | 19572 / 10728 / 4422 / 15150（店） |

这印证了 v3 spec §15.1 的未决决策 **D1**（验收基准数字）。**本设计不解决 D1，只声明验收方式为一致性断言。**

---

## 3. 边界

### 3.1 做

- 一个独立可运行的**最小 MCP 服务**（streamable HTTP，本机）。
- 两个只读工具（`visit_ping`、`visit_month_summary`）。
- Bearer 鉴权 + 三种凭据通道的可观测性（用于诊断 F11）。
- 请求级证据日志。
- 一份 P0 实测记录，含协议层事实与对 v3 spec 的修订建议。
- 给用户的连接器配置值 + 人工步骤说明。

### 3.2 不做（本轮硬边界）

- **不改** `app/` 下任何文件、`requirements-web.txt`、`migrations/`、项目 `.venv`。
- **不碰** 线上 `store-prod`、不做任何发布。
- **不做任何写操作**：SQLite 以 `mode=ro` 打开，结构上不可写。
- **不实现** `api_tokens`、`mcp_audit_log`、`sealed_months`、封账、预演、确认语（P1/P2）。
- **不实现**上传通道、员工自助、调度器（v3 spec §16 的 YAGNI 清单继续有效）。
- **不重构**现有 66 个路由。

---

## 4. 设计

### 4.1 运行时与依赖

| 项 | 选择 | 理由 |
|---|---|---|
| 解释器 | Homebrew `python3.12` | F1/F3：项目 venv 3.9.6 装不了 `mcp` |
| 依赖环境 | `spike/mcp/.venv`（**独立于项目 venv**，gitignore） | 零污染项目环境，不动现有 101 个测试 |
| 依赖 | `mcp`（安装时确定版本并**精确锁定**写入 README 与记录） | 优先最新稳定版；若与客户端协议不兼容（F7）则降级并记录理由 |
| 数据 | SQLite `file:<abs>store_settle_live.db?mode=ro` | 只读 URI，不可能写坏数据 |
| 传输 | streamable HTTP | 与终局形态（客户端 ↔ 客户环境内的服务）同构 |
| 监听 | `127.0.0.1`，端口默认 `8765`（`MCP_SPIKE_PORT` 可覆盖） | 最严可达性；端口可换 |

不使用项目 venv、不 import `app.*`：spike 服务只依赖 `mcp` 与标准库（`sqlite3`/`hmac`/`json`/`logging`），**与主应用完全解耦**。

### 4.2 目录结构

```
spike/mcp/
  server.py         # MCP 服务：鉴权、日志、两个工具
  run.sh            # 建/用 .venv 并启动（一条命令）
  README.md         # 如何跑、要填给 WorkBuddy 的值、SDK 版本锁定记录
  .env.example      # VISIT_SPIKE_TOKEN / MCP_SPIKE_PORT 示例
  .venv/            # gitignore
  logs/             # gitignore，请求级证据
docs/workbuddy-p0-验证记录.md   # P0 实测记录（验收后填）
```

`spike/` 是**一次性探测代码**，P1 开始时其结论并入 `app/mcp/`，本目录删除——README 中明确标注，避免被误认为产品代码。

### 4.3 服务端行为

**鉴权。** 三种凭据通道**同时接受**，优先级如下，且**每次请求记录实际命中的通道**：

1. `Authorization: Bearer <token>`
2. `X-Visit-Token: <token>`
3. 查询参数 `?token=<token>`

用 `hmac.compare_digest` 比较。全部缺失或不匹配 → **401** + `WWW-Authenticate: Bearer`，响应体为 §4.4 的错误信封。

> 同时接受多通道的唯一目的是把 F11（`headerOverridesBearerStripped`）的判定压缩成**一次往返**：若通道 1 失效而通道 2 有效，就确定是 Bearer 被剥离，而不是端点不可达。
> **安全标注**：查询参数通道会把凭据写进 URL 与日志，**仅限本机 spike**；P1 必须移除，只保留单一通道。

**为什么必须记录"命中的通道"**：客户端界面报错通常只显示"连接失败"，无法区分"端点不可达 / 凭据被剥离 / 凭据错误"。服务端日志是唯一能给出确定结论的证据源。

**日志。** 每个请求一行 JSON 落到 `spike/mcp/logs/requests.jsonl`：`ts`、`method`、`path`、`header_names`（**只记名字，不记值**）、`auth_channel`（`bearer`/`x-visit-token`/`query`/`none`）、`auth_ok`、`protocol_version`（请求头 `mcp-protocol-version`）、`mcp_session_id`（有无 `Mcp-Session-Id`）、`status`、`duration_ms`、`body_digest`。凭据值一律不落日志。

**协议协商。** 使用 SDK 默认协商；不硬编码 protocolVersion。日志记录实际协商结果（F7 的诊断依据）。

**Host 校验。** 本地 spike 允许 `127.0.0.1`/`localhost` 作为 Host，禁用会阻断本地连通的 DNS-rebinding 保护；在 README 中标注该放宽仅限 spike。

### 4.4 工具契约

统一信封（对齐 v3 spec §8）：

```
成功: {ok: true,  data: {...}}
失败: {ok: false, error: {code, message, hint}}
```

**工具 1 · `visit_ping`**（诊断用，P1 起删除）

```
visit_ping() -> {ok:true, data:{
  server: "visit-settle-spike",
  sdk_version: "...",
  protocol_version: "<协商结果>",
  client_info: "<客户端上报名称/版本>",
  auth_channel: "bearer|x-visit-token|query",
  auth_header_present: true|false,     # 是否收到过 Authorization 头（即使不匹配）
  session_id_present: true|false,
  now: "<ISO8601>"
}}
```

描述写中文："诊断用：回显服务端身份、协商到的协议版本、以及本次调用实际使用的凭据通道。用于排查 WorkBuddy 连接与鉴权问题。"

**工具 2 · `visit_month_summary`**（唯一的真实数据工具）

```
visit_month_summary(month: str) -> {ok:true, data:{
  month: "2026-09",
  formal_rows: 15067,
  points_total: 19471,
  p1_count: 10663,
  p2_count: 4404,
  persons: 34
}}
```

参数 `month` 用 JSON Schema `pattern: ^[0-9]{4}-(0[1-9]|1[0-2])$`，越界/格式错 → `BAD_MONTH`（复用 v3 spec §7.2 的严格口径：`\d` 会放行全角而静默返回 0 行）。该月无数据 → `ok:true` 且各计数为 0，并附 `hint`（**不报错**：无数据是合法查询结果，不是失败）。

描述写中文："查询某结算月（格式 YYYY-MM）正式表的行数、总点数、1点/2点条数与人数。只读。"

**错误码**（本 spike 只用三个）：

| code | 触发 | hint |
|---|---|---|
| `UNAUTHORIZED` | 三种通道均缺失或不匹配 | "请在 WorkBuddy 连接器设置中重新填写 Access Token" |
| `BAD_MONTH` | 月份格式非法 | "月份必须是 YYYY-MM，例如 2026-09" |
| `INTERNAL` | 未预期异常（含 SQLite 打不开） | "系统内部错误，已记录；可重试" |

异常必须被捕获并转成信封，不允许穿透到协议层（v3 spec §8.2；只读工具故用 `INTERNAL` 而非 `INTERNAL_WRITE`）。

### 4.5 用户侧动作（人工，不可替代）

| 步 | 动作 | 卡点 |
|---|---|---|
| 0 | 在 WorkBuddy GUI 里找到「自定义连接器」入口并确认**可用**（F10 企业策略可能拦截），告知我它要求填哪些字段 | **阻塞**：这步不通，P0 无法继续 |
| 1 | 按我给出的值填 URL 与 header/token | |
| 2 | 让 agent 调用两个工具，把**界面原文**（成功或报错）反馈给我 | |

第 0 步若被企业策略拦截，退路见 §6。

---

## 5. 验收标准

| # | 标准 | 判定方式 |
|---|---|---|
| A1 | WorkBuddy 能连上并**列出两个工具**（中文描述完整） | 客户端界面可见 |
| A2 | 正确凭据调用 `visit_month_summary("2026-09")` 返回的数字**与 SQL 直查逐项一致**（15067 / 19471 / 10663 / 4404 / 34） | 与 §2.1 基线逐项比对 |
| A3 | 错误凭据被拒，且**客户端可见**拒绝、**服务端日志**有对应 401 | 两侧同时成立才算通过 |
| A4 | 记录握手、认证、报错的**真实表现**；给出协议层会话事实（客户端是否发 `Mcp-Session-Id`、是否要求有状态），并明确说出 D2 的哪一半**未**被回答 | `docs/workbuddy-p0-验证记录.md` |

A3 的"两侧同时成立"是刻意的：只看到客户端报错不足以判断是鉴权失败还是协议失败。

---

## 6. 风险与退路

| # | 风险 | 探测方式 | 退路 |
|---|---|---|---|
| R1 | 企业策略禁止自定义连接器（F10） | 人工步 0 | 直接写 `~/.workbuddy/connectors/<uuid>/mcp.json`；受限于加密的 `enabled` 列表（F12），若无法使能则换账号或换一台非企业策略管控的机器 |
| R2 | **Bearer 被剥离（F11）** | `visit_ping` 回显 + `auth_channel` 日志 | 改用 `X-Visit-Token` 或 `?token=`（服务端已同时支持，无需改代码） |
| R3 | `127.0.0.1` 不可达 | 直接连 | 绑 `0.0.0.0` + 局域网 IP（同机不同网卡）；或改 stdio 本地进程（客户端已支持 stdio，F5/F6） |
| R4 | 协议版本不匹配 | 握手日志 | 调 SDK 版本或显式指定 `protocolVersion` |
| R5 | 端口占用 | 启动时检测并报错退出 | 换 `MCP_SPIKE_PORT` |
| R6 | SDK 与客户端行为差异导致静默失败 | 官方 MCP **Python client SDK** 先做端到端自测 | 自测通过而客户端不通 → 问题定位在客户端侧，而非服务端 |

R6 是这次设计的分界线：**先用官方 client SDK 证明服务端是对的，再去测客户端**。否则客户端不通时无法归因。

---

## 7. 测试方式

| 层 | 方式 | 断言 |
|---|---|---|
| T1 服务端正确性 | 官方 MCP Python client SDK 连本机端点（正常凭据） | 工具清单 = 2 个且描述非空；`visit_month_summary` 数字与 SQL 一致 |
| T2 传输层 | `curl` 直接打 HTTP 端点 | 无凭据 → 401 + `WWW-Authenticate`；错凭据 → 401；对凭据 → 握手成功 |
| T3 凭据通道 | `curl` 分别用 Bearer / `X-Visit-Token` / `?token=` 调用 `visit_ping` | 三种通道各自的 `auth_channel` 回显正确 |
| T4 数据只读 | 尝试对连接执行写语句（测试内） | SQLite 报只读错误，证明不可能写坏数据 |
| T5 端到端 | WorkBuddy 桌面客户端（人工） | 满足 A1–A3 |

T1–T4 不依赖 WorkBuddy，可在无人参与时跑完；T5 需要用户。

---

## 8. 明确不做（YAGNI）

- 不把 spike 挂进 FastAPI（方案 B，用户已选择 A 先行）。
- 不用 Node/TS 重写服务端（方案 C）。
- 不做鉴权以外的任何闸门（封账、预演、确认语）。
- 不做审计表落库（只写文件日志）。
- 不做连接器包（`skills/SKILL.md`）——"表达形式"是 P1 议题，P0 只到"工具能被发现并调用"。
- 不解决 D1（验收基准数字）。

---

## 9. 待回写 v3 spec 的修订项（P1 输入，本轮只记录）

P0 完成后，v3 spec 有**两处必然需要修订**（用户 2026-09-22 的两条新约束直接推翻原文）：

1. **§2「上传留在网页」** → 若网页可下掉，上传必须在 MCP 侧有等价物。"远端服务拿不到本地 Excel"这个理由，在"客户端与系统同处客户内网"的形态下**不再成立**。
2. **§2/§3「远端 MCP 挂在公网 `store-prod`」** → 与"数据不出客户边界"冲突。终局应是 MCP 随系统部署在客户环境内，客户端经内网直连。

另有两处受 P0 证据影响、待定：

3. **§6 鉴权**：若 R2 成立（Bearer 被剥离），Token 注入通道需要重定。
4. **§7.6 多 worker**：本 spike 为单进程，只能给协议层事实；D2 需 P1 补完。

---

## 10. 未验证假设

| 假设 | 风险 | 验证方式 |
|---|---|---|
| 客户端能连本机 `127.0.0.1` 的 HTTP 端点 | 若客户端把 MCP 流量走云端网关，则私有化路线不成立 | P0 T5（本文核心） |
| 自定义连接器在**企业账号**下默认可用 | F10 显示受后台策略管控，可能被禁 | 人工步 0 |
| 客户端会原样转发自定义 header | F11 显示曾收紧过 Bearer | T3 + T5 |
| 客户端工具发现不依赖连接器包（`mcp.json` 之外的 `skills/`） | 若必须成包，自定义连接器的"表达形式"受限于官方包格式 | T5 观察是否直接列出裸工具 |
| 本机 spike 结论可外推到客户内网部署 | 内网可能有出口代理/防火墙策略差异 | 无法在本轮验证，记录为明确假设 |

---

## 附注

- F15 的全路由清点另存于 `/tmp/wb_inventory_routes.md`（289 行，临时文件；关键结论已摘要进 §2）。
- 本设计不修改 v3 spec 原文；修订在 P1 进行，避免在证据不足时改写定稿文档。
</content>
