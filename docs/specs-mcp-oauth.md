# MCP OAuth（SSO）规格

> **状态**：设计规格（该期定稿）｜**最后更新**：2026-10-06
> **一句话**：MCP OAuth（SSO）

> 目标：员工在 WorkBuddy 点「连接」→ 浏览器登录**巡店系统账号** → 自动完成授权。
> **不再需要每人手动配 token**。已确认 WorkBuddy 客户端支持 MCP OAuth（实测其主程序含
> `/.well-known/oauth-protected-resource`、`pkce`、`registration_endpoint`、`token_endpoint`）。

## 一、设计要点：OAuth 只是"签发 token 的另一种方式"

换出来的 access token **就是现有 `api_tokens` 里的一行**，因此**授权模型零改动**：
`Actor`（身份/角色/scope）→ 授权矩阵（越权拦截）→ 两阶段审计 → **员工状态联动（休假即失效）**
全部自动继承。`/my/token` 自助签发仍保留（供无浏览器/自动化场景）。

## 二、端点（服务端要实现的 6 件）

| # | 端点 | 作用 |
|---|---|---|
| 1 | `GET /.well-known/oauth-protected-resource` | 声明 resource + authorization_servers + scopes_supported |
| 2 | `GET /.well-known/oauth-authorization-server` | 声明 issuer 与各端点、`code_challenge_methods_supported=["S256"]`、`grant_types_supported=["authorization_code","refresh_token"]` |
| 3 | `POST /oauth/register` | 动态客户端注册（DCR）：接收 redirect_uris/client_name → 返回 client_id（+ 可选 client_secret） |
| 4 | `GET /oauth/authorize` | 授权码流程：校验参数 → **未登录则跳现有登录页**（带回跳）→ 已登录则显示**授权确认页**（客户端名 + 申请 scope）→ 同意后发 code（5 分钟有效，绑定 PKCE challenge） |
| 5 | `POST /oauth/token` | `authorization_code`：校验 PKCE（S256）→ **创建 api_tokens 行** → 返回 access_token；`refresh_token`：换新 access_token 并**轮换** refresh token |
| 6 | MCP 端点 401 | 未认证时返回 `WWW-Authenticate: Bearer resource_metadata="<公网>/.well-known/oauth-protected-resource"`（客户端据此自动发起授权） |

## 三、新增表（迁移）

```
oauth_clients(id, client_id UNIQUE, client_secret_hash NULL, client_name,
              redirect_uris TEXT(JSON), created_at, last_used_at)
oauth_codes(id, code_hash UNIQUE, client_id, user_id, redirect_uri,
            code_challenge, code_challenge_method, scope,
            expires_at, used_at NULL, created_at)
oauth_refresh_tokens(id, token_hash UNIQUE, client_id, user_id, scope,
                     expires_at, revoked_at NULL, created_at, last_used_at)
```

## 四、Token 生命周期

| 项 | 取值 |
|---|---|
| access_token | 复用 `api_tokens`，**24 小时**（短寿命；由客户端用 refresh 续） |
| refresh_token | 90 天，**每次刷新轮换**（旧的置 revoked_at） |
| scope | `read`（员工）/ `read,write`（管理员按角色上限；**请求范围不得超过角色允许**） |
| 失效 | 与现有规则一致：吊销 / 过期 / 员工非 active（休假即失效） |

**scope 上限规则**：员工申请 `write` → 降级为 `read`（不报错，按最小权限授予）；管理员按请求授予。

## 五、安全要求（必须做到）

1. **PKCE 强制**：`code_challenge_method` 必须 `S256`，否则 `invalid_request`。
2. **code 一次性**：用过即 `used_at`，重复使用 → `invalid_grant`（且**吊销该 code 换出的 token**，防重放）。
3. **redirect_uri 必须与注册时完全一致**（不做前缀/通配匹配）。
4. **code 5 分钟过期**；**access 24h / refresh 90d**。
5. **state 原样回传**（不解析、不记录）。
6. **授权确认页**必须展示：客户端名称 + 申请的 scope（让用户知情）；拒绝 → `error=access_denied`。
7. **登录复用**：未登录时跳现有登录页（`/login?next=<authorize URL>`），登录后回到 authorize。
8. **不记录任何 token 明文**（库内只存 hash；审计里脱敏）。
9. **CSRF**：确认页提交需带 CSRF（沿用项目既有 `csrf_ok`）。
10. `client_secret`（若发放）只显示一次、库内 hash。

## 六、验收（必须能证明）

1. 完整授权码 + PKCE 流程可跑通：discovery → register → authorize（已登录）→ token → **用换出的 token 调 MCP 工具成功**。
2. **未登录访问 authorize → 跳登录页**；登录后继续并拿到 code。
3. **PKCE 校验失败**（错误 verifier）→ `invalid_grant`。
4. **code 重放** → 第二次 `invalid_grant`，且第一次换出的 token 被吊销。
5. **redirect_uri 不匹配** → `invalid_request`（不发 code）。
6. **员工申请 write → 实际只拿到 read**（调写工具被 FORBIDDEN_TOOL）。
7. **refresh 轮换**：旧 refresh 再用 → `invalid_grant`；新 access 可用。
8. **员工状态改 leave** → 换出的 token 立即失效（复用现有联动）。
9. 审计：OAuth 换出的 token 调用工具时，`mcp_audit_log` 有 `user_id`（与 `/my/token` 签发的无差别）。

## 七、配置

| 环境变量 | 说明 |
|---|---|
| `VISIT_OAUTH_ISSUER` | 对外 issuer（默认取 `VISIT_MCP_PUBLIC_HOST` 的 https 地址） |
| `VISIT_OAUTH_ENABLED` | 默认开；`0` 可关（关后 authorize/token 返回 404，仍可用 `/my/token`） |
| `VISIT_OAUTH_ACCESS_HOURS` / `VISIT_OAUTH_REFRESH_DAYS` | 默认 24 / 90 |

## 八、约束

- 不破坏现有：`/my/token`、静态 token（本地测试）、既有鉴权与授权矩阵、MCP 工具签名。
- OAuth 端点挂在**现有 web 应用**上（同一域名，便于复用登录/CSRF/模板）；MCP 服务只负责 401 头。
- 生产经 nginx：`/.well-known/*`、`/oauth/*` 由 web 提供；`/mcp` 由 MCP 服务提供。
