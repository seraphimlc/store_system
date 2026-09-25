# WorkBuddy × 巡店结算系统 · P0 验证记录

- 日期：2026-09-22（首轮实测）
- 分支：`feat/workbuddy-integration`
- 对应设计：`docs/superpowers/specs/2026-09-22-workbuddy-p0-connectivity-spike-design.md`
- 结论：**P0 通过**。WorkBuddy 桌面端可经 URL 型自定义连接器连上本机 MCP 端点、列出工具、调用工具并取回与库一致的真实数据。

---

## 1. 验收清单（spec §7.2 A1–A6）

| # | 标准 | 结果 | 证据 |
|---|---|---|---|
| A1 | 客户端能连上并列出工具 | ✅ | 用户在 WorkBuddy「自定义连接器」注册后，界面可见 **3 个工具**：`visit_ping` / `visit_month_salary` / `visit_month_summary`（中文描述完整） |
| A2 | 真实数据正确 | ✅ | 问「小川逸 9 月工资」→ 返回 **工号 2188240606634082 / 1410 点 / 单价 250 / 1点456条 / 2点477条 / 375,000 円**；与 `month_perf_records` 直查一致（9 月合计 34 人 / 19471 点 / **5,171,500 円**） |
| A3 | 错误凭据被拒 | ✅（服务端侧） | 无 Token 请求 → **401** + `WWW-Authenticate: Bearer`；`mcp_service/logs/requests.jsonl` 有对应 401 行。客户端侧未专门构造错 Token（用户未测） |
| A4 | 凭据头透传 | ✅ | 日志：成功请求 `auth_header_seen=true`，无 Token 请求为 false 且 401 → **`Authorization: Bearer` 确实原样到达服务端**（E10 的"Bearer 被剥离"风险未发生） |
| A5 | 握手事实留档 | ✅ | 见 §2 |
| A6 | 会话结论（D2） | ✅（部分） | 见 §3 |

---

## 2. A5 · 握手与协议事实（取自服务端请求日志，真实客户端行为）

日志：`mcp_service/logs/requests.jsonl`（84 条请求，含首轮 curl 自测）

| 事实 | 实测值 |
|---|---|
| `initialize` 是否成功 | 成功（`POST /mcp` → 200/202 后进入工具调用） |
| 协商到的 `protocolVersion` | **`2025-11-25`**（客户端支持列表的上限，也是服务端握手路径上限） |
| 是否带 `Mcp-Session-Id` | **是**：72/84 条请求带该头 → 客户端使用**有状态**会话 |
| 会话生命周期 | 完整：`POST`（initialize）→ `POST`（带 session id 调用）→ `GET /mcp`（SSE 流，status 200）→ `DELETE /mcp`（结束会话，status 200） |
| 尾斜杠 | 客户端请求路径为 `/mcp`（无尾斜杠），与 `streamable_http_path="/mcp"` 一致 |
| `Accept` 头 | 客户端发 `application/json, text/event-stream`（由 SDK 默认行为满足，无需服务端特殊处理） |
| `Origin` 头 | **客户端不发 Origin** → 与 F11 的"缺失 Origin 放行"一致；`allowed_origins=[]` 是正确配置 |
| `Host` 头 | `127.0.0.1:8765` → 命中 `allowed_hosts=["127.0.0.1:*", ...]`（**DNS-rebinding 保护保持开启**，无需关闭） |
| 结果 surface 形式 | 工具返回同时含 `structuredContent` 与文本；客户端能正确读取（agent 输出结构化数字，说明 `structuredContent` 被采纳） |

---

## 3. A6 · D2（多 worker 与会话）结论

- **能判定**：客户端**要求有状态会话**（发 `Mcp-Session-Id`、有 SSE 流与 DELETE 结束会话）。因此：
  - v3 §7.6 的**方案 a（SDK 无状态模式）**在 SDK 侧存在（`MCPServer.run(..., stateless_http=)`，P0 事实 F10），但**本客户端走的是有状态路径**，切换无状态需验证客户端是否接受；
  - **方案 b（独立进程/容器，`--workers 1`）成立且已采用**：P0/P1 的 `mcp_service/` 就是独立进程，本机单进程实测通过。
- **不能判定**：多副本/多 worker 下的会话归属（本机为单进程，未构造多 worker 场景）→ 留 P1 生产部署时用 `stateless_http=True` 验证。

---

## 4. 未验证假设的更新（spec §11）

| # | 假设 | 结论 |
|---|---|---|
| U1 | 第三方/自定义连接器**允许用户自填 URL** | ✅ **已确证**：用户以 **URL 形态**（`http://127.0.0.1:8765/mcp` + Bearer 头）注册成功并跑通。**R1 风险（"市场不允许自填 URL"）对本形态不成立**；市场审核口径仍待 open.workbuddy.cn 后台确认（只影响上架，不影响私有部署） |
| U2 | 客户端能连本机 `127.0.0.1` 端点 | ✅ **已确证**：本机回环地址可连（客户端直连，不经云端网关） |
| U3 | 客户端会原样转发 `Authorization` 头 | ✅ **已确证**（见 A4） |
| U4 | 工具发现不依赖额外清单文件 | ✅ 自定义连接器仅凭 `mcpServers` 配置即列出工具 |
| U5 | 本机单进程结论可外推生产多副本 | ⏳ 待 P1 生产验证（`stateless_http`） |

---

## 5. 过程中的真实坑（供后续参考）

1. **客户端会发起完整会话生命周期**：`POST initialize → POST(带 session) → GET(SSE) → DELETE`。任何"只处理 POST"的自研 MCP 实现都会不兼容；官方 SDK 无此问题。
2. **客户端不发 `Origin`**：若把 DNS-rebinding 保护的 `allowed_hosts` 配成空列表会拒绝一切请求（P0 事实 F11 的坑），显式配 `127.0.0.1:*` 即可，**不需要关闭保护**。
3. **金额单位必须显式给**：首轮 agent 把日元表述成"¥375,000 元"。已在数据层加 `currency: "JPY"` 字段并在工具描述与 SKILL 里写明"日元（円）"。
4. **本机 npm 缓存有 root 属主文件**：`npx` 报 EPERM（`~/.npm/_cacache`）。因此 stdio 桥方案改为**原生 stdio**（`server.py --stdio`），不依赖 npm。
5. **首次 import 冷缓存极慢**：`import mcp` 首次需 ~115 秒（字节码编译），热缓存 0.6 秒 → 服务端就绪等待类测试必须给足超时（已从 20s 提到 90s）。

---

## 6. 结论与后续

- **P0 目标达成**：连通性与凭据透传两个未知全部消除，且已产出可用能力（9 月薪资查询）。
- **形态确认**：远端 streamableHttp + URL 型自定义连接器**可用**（私有部署路径成立）；stdio 作为备用形态也已实现并实测。
- **下一步**：按用户目标"先打通一条链路，再补其它能力"——继续补只读能力（人员明细 / 对账差异 / 找平），随后再考虑写工具（P1 spec 已定稿）。
