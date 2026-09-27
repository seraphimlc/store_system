# MCP 身份认证规格（每用户一身份）

> 用户需求（2026-09-25）：从"共享固定 token"改为"每个用户在 WorkBuddy 里绑定自己的身份"。
> 现状：静态 token（无身份）+ `api_tokens` 表/`tokens.py`（已具备绑定到人、hash 存储、吊销）。

## 一、需求（用户原话逐条落地）

| # | 需求 | 落地规则 |
|---|---|---|
| 1 | 普通员工在 WorkBuddy 里以员工身份**看自己的绩效** | staff 角色可用"我的"系列工具，**只能看自己**（按 `user.person_code` 强制过滤） |
| 2 | 管理员做所有管理员操作 | admin 角色全开（现有 45 工具） |
| 3 | **普通员工越权一定要能拦截住** | 中央授权矩阵：每个工具声明所需角色/scope；staff 调 admin 工具 → `FORBIDDEN_TOOL`（结构化、retryable=false）。**默认拒绝**（未登记的工具默认 admin-only） |
| 4 | 员工自己管理 token；**token 状态跟员工状态一致**（休假→token 也休假） | 解析 token 时校验绑定用户：`status == "active"` 且 `is_active` 才有效；leave/disabled/resigned → `UNAUTHORIZED`（附原因） |
| 5 | 有效期听建议 | 默认 **90 天**（`expires_at`），管理员可签永久（`expires_at=NULL`） |
| 6 | 读写全记 | **所有调用**两阶段审计（先插 `ok=NULL` → 返回前回填），含 `user_id/token_id/tool/params摘要/ok/error_code/耗时` |
| 7 | 先手动连接，后面做成"应用连接" | 本期：用户自助签发 token → 粘贴进 WorkBuddy 连接器 headers。后续工作项：应用级连接（OAuth 类） |

## 二、角色与工具矩阵

**staff（员工）可用**（只读、仅自己）：
- `visit_my_perf(month=None)` —— 我的绩效（月点数/工资/1点2点）
- `visit_my_daily(month)` —— 我的日明细
- `visit_my_settlement()` —— 我的找平状态/已发/待扣（找平表 + 台账 + 关联）
- `visit_ping` / `visit_config_get`（非敏感）/ `visit_product_doc`
- `visit_my_token_*`（自助管理，见第四节）

**admin 专属**：其余全部（上传/入表/重算/改单价/配置/店铺主档/员工管理/公司级查询与导出/对账/找平写操作）。

**拦截规则**：
- staff 调 admin 工具 → `FORBIDDEN_TOOL`，hint 说明"该操作仅管理员"。
- staff 调"我的"工具时若显式传 `person`/`person_code` 且不是自己 → `FORBIDDEN_TOOL`（**不能靠客户端自律，服务端强制覆盖为本人**）。
- 读工具也不放行公司级数据（如 `visit_month_salary` 对 staff 直接拒绝，而非过滤——避免"能调但空"的困惑）。

## 三、Token 生命周期

- 签发：`tokens.issue(user_id, name, scopes, days=90)` → 返回明文一次（仅此一次），库内只存 `token_prefix` + `sha256`。
- 校验（每次调用）：存在 ∧ `revoked_at IS NULL` ∧ (`expires_at IS NULL` ∨ 未过期) ∧ 绑定用户 `is_active` ∧ 用户 `status == "active"`。
- 失效原因区分（便于排查）：`revoked` / `expired` / `user_inactive` / `user_status:<leave|disabled|resigned>`，写进审计 `detail`，对外统一 `UNAUTHORIZED` + hint。
- 静态 token：本地测试保留（`VISIT_MCP_STATIC_TOKENS=1`）；**生产关闭**。

## 四、页面

- `/my/token`：登录用户自助——生成（名称 + 有效期 + scope 只读/读写，明文仅显示一次）、列表（前缀/名称/scope/创建/最后使用/状态）、吊销。
- 管理员：`/staff-admin` 可代发/吊销任意员工的 token；员工状态变更后页面提示"其 token 已随之失效"。
- `/mcp-audit`（管理员）：按人/工具/时间查调用记录（含失败与拒绝）。

## 五、验收（必须能证明"拦得住"）

1. staff token 调任一 admin 工具 → `FORBIDDEN_TOOL`，且**无副作用**（数据不变）。
2. staff token 调"我的"工具 → 只返回本人数据（构造两个员工，互查不到）。
3. staff 显式传别人的 `person` → `FORBIDDEN_TOOL`（不是静默过滤）。
4. 员工 status 改为 `leave` → 其 token 立即失效（`UNAUTHORIZED` + 原因 leave）；改回 `active` → 恢复。
5. 员工 `disabled`/`resigned` → token 失效。
6. 吊销 / 过期 → 失效。
7. 每次调用（含被拒绝）在 `mcp_audit_log` 有一行，含 `user_id`/`token_id`/`tool`/`ok`/`error_code`。
8. 管理员全量操作不受影响（45 工具回归全绿）。

## 六、约束

- 不破坏现有行为：admin 路径与工具签名不变；静态 token 本地可用。
- 授权**默认拒绝**：新增工具必须显式登记角色，否则仅 admin 可用。
- 审计**不含 Token 明文**；参数摘要脱敏（如 content_base64 只记长度）。
