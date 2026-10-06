# MCP 对接手册（可复用）

> **状态**：活文档（可复用）｜**最后更新**：2026-10-06
> **一句话**：对接其它系统时读：协议实测/OAuth 坑/工具设计/发布验收

> 来源：巡店结算系统接入 WorkBuddy 的完整实践（P0 连通性 → P1 写工具 → 身份认证/SSO → 上线）。
> **用途**：其它业务系统接入 MCP 客户端（WorkBuddy 等）时，直接照本手册执行，避免重踩。
> 本手册只写**通用做法与实测事实**；本系统的具体实现见 `docs/索引.md §1.5` 与各 spec。

---

## 0. 一句话路线

```
独立 MCP 服务进程（复用业务代码，不重写业务）
  → **工具按"业务场景"切，不按 REST 接口切**（见 §4.6，第一版踩过的最大坑）
  → 只读工具先行（打通链路）
  → 写工具 + 闸门（危险操作要确认/预演）
  → 统一错误信封 + 工具描述（LLM 友好）
  → 每用户身份（OAuth/SSO）+ 授权矩阵 + 审计
  → 部署（同镜像独立服务 + 反代）+ 迁移/回填 + 验收
```

---

## 1. 架构：MCP 服务怎么放

| 决策 | 做法 | 理由 |
|---|---|---|
| 进程 | **独立进程/容器**，与 web 应用共存 | 故障隔离；MCP 崩溃不影响业务页面 |
| 代码复用 | MCP 层 `import` 业务服务层（`app.services.*`） | **绝不重写业务逻辑**（判定/工资/对账出现第二份实现＝灾难） |
| 依赖 | 独立 venv/镜像，MCP SDK 要求 **Python ≥ 3.10** | 老项目主 venv 可能是 3.9（本系统就是） |
| 传输 | **streamable HTTP**（主）+ stdio（本地/无端口场景） | 远端接入用 HTTP；客户端能拉起本地进程时用 stdio |
| 启动前 | **必须显式设 `DATABASE_URL`** | 很多框架的 settings 带 `lru_cache`，import 时固化 → 不设会静默连到空库 |
| 路由 | MCP 服务只负责 `/mcp`（+ 发现端点）；OAuth/登录页挂在 web 应用 | 复用既有登录/CSRF/模板 |

**反向代理要点**（Nginx）：MCP 用 SSE，必须

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:<mcp端口>;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;        # 关键：不关缓冲 SSE 会被卡住
    proxy_cache off;
    proxy_read_timeout 3600s;   # 长连接
    proxy_send_timeout 3600s;
    client_max_body_size 60m;   # 文件上传
}
```

---

## 2. 客户端（WorkBuddy）实测事实

| 事实 | 说明 |
|---|---|
| 连接器类型 | `streamableHttp` + `url`（**自定义连接器**可用；企业策略需允许） |
| 协议版本 | 客户端支持到 **2025-11-25**；服务端按客户端上限协商 |
| 会话 | **有状态**：`Mcp-Session-Id` 头、GET SSE、DELETE 结束 |
| Origin | 客户端**不发 Origin** → 服务端的 DNS-rebinding 白名单要放行"缺失 Origin" |
| Host 校验 | 经反代时 Host 是域名 → **必须把域名加入 allowed_hosts**（否则直接拒） |
| 凭据存储 | 客户端按账号**加密存储**在本机（`~/.workbuddy/connectors/<id>/`） |
| 工具清单缓存 | 客户端**缓存 tools/list** → 服务端改了工具要**重连**才生效（数量与档位：员工 6 / 队长 9 / 管理员 24） |
| 工具级过滤 | 客户端支持 `disabledTools`（可用于按连接器屏蔽工具） |
| OAuth | **支持 MCP OAuth**：`/.well-known/oauth-protected-resource`、PKCE、动态注册、`token_endpoint`、device_code |

---

## 3. OAuth / SSO（最容易踩的一段）

### 3.1 结论：OAuth 只是"签发 token 的另一种方式"

把换出的 access token 落进**既有的 token 表**（绑定用户、hash 存储、可吊销），于是
**身份 / 权限 / 审计 / 状态联动全部自动继承**，权限模型零改动。

### 3.2 服务端要实现 6 件

| # | 端点 | 要点 |
|---|---|---|
| 1 | `GET /.well-known/oauth-protected-resource` | `resource` = **受保护资源（MCP 服务）地址**；`authorization_servers` = 授权服务器（可不同源） |
| 2 | `GET /.well-known/oauth-authorization-server` | issuer + 各端点 + `code_challenge_methods_supported=["S256"]` + `grant_types_supported` |
| 3 | `POST /oauth/register` | 动态客户端注册（DCR），返回 `client_id` |
| 4 | `GET/POST /oauth/authorize` | 未登录 → 跳既有登录页（带回跳）；已登录 → **授权确认页** → 发 code |
| 5 | `POST /oauth/token` | `authorization_code`（校验 PKCE）→ 发 access；`refresh_token` → 轮换 |
| 6 | MCP 端 401 | `WWW-Authenticate: Bearer resource_metadata="<MCP自身>/.well-known/oauth-protected-resource"` |

### 3.3 五个致命坑（都实测踩过）

1. **`resource` 必须与客户端连接的 MCP URL 同源**（origin 精确比对，端口不同即拒）
   → 症状：**"点连接没反应"、无任何报错**。不要把 `resource` 写成 web/授权服务器的地址。
2. **发现端点必须公开**（不能要求鉴权）——否则客户端拿到 401，无法开始授权。
3. **授权服务器可以与资源不同源**：`resource=http://mcp:8765` + `authorization_servers=[http://web:8000]` 是合法的；
   但**401 里的 metadata URL 指向 MCP 自身**最稳（客户端默认按资源 origin 取元数据）。
4. **回调是自定义协议**（如 `workbuddy://.../oauth/callback`）→ 浏览器打开它时**看起来像卡住/无反应**，
   其实客户端已收到回调并完成换 token。**判断成功与否要看服务端**（是否签发 token、是否在使用）。
5. **授权环节要校验账号状态**（在岗/请假/停用）：否则"授权成功但 token 永远不可用"，体验矛盾。

### 3.4 安全清单（必须做到）

- PKCE **强制 S256**；code **一次性**（重放要**吊销已换出的 token**）
- `redirect_uri` **精确匹配**（不做前缀/通配）
- code 短寿命（5 分钟）；access 短寿命（24h）；refresh 长寿命（90d）且**每次轮换**
- **scope 上限**：按角色降级（员工申请写 → 只给读，不报错）
- 库内只存 hash；审计与日志**绝不含 token 明文**
- 授权确认页展示：**客户端名 + 以哪个账号授权 + 权限范围（按角色写成能力清单）**

### 3.5 切换账号

客户端缓存凭据与工具清单 → 换身份通常需要**删除连接器再重新添加**（或客户端提供的重连）。
服务端可加"不是这个账号？切换"链接（退出并回到授权流程）作为可选增强。

---

## 4. 工具设计（LLM 友好 = 调用成功率）

### 4.1 描述模板（每个工具都要写全）

```
<一句话：做什么>
什么时候用：<与相似工具的区分——互斥指引>
关键约束：<只读/写、幂等性、覆盖语义、金额单位、参数格式>
```

**为什么**：模型面对多个"看起来都能答"的工具会选错；**互斥指引**是调用成功率的最大杠杆。

### 4.2 统一错误信封（必须有 `retryable`）

```json
{"ok": false, "error": {"code": "INTERNAL_WRITE",
                        "message": "写入异常", "retryable": false,
                        "hint": "可能已部分生效，**不要自动重试**"}}
```

- 成功 `{"ok": true, "data": {...}}`；失败**不抛裸异常**（模型要能分支处理）
- **`retryable` 是关键**：写操作可能已部分生效 → `false`；读侧临时故障 → `true`
- 描述里给**错误码表**（含义 + 可否重试）

### 4.3 危险语义必须前置声明

- **覆盖/去重**：同月重传是覆盖还是追加？文件指纹去重？
- **幂等性**：同参数重复调用有无副作用（写工具要标 `idempotentHint`）
- **两段式**：危险操作先"预演"（返回影响面）→ 再"执行"（带确认语/预览 id）
- **`dry_run`**：只预估不写入（尤其"导出即产生副作用"这类语义要能预演）

### 4.4 自动识别 vs 让用户选

文件类入口**不要让模型判断走哪个通道**：服务端嗅探（sheet 名/表头特征）→ 路由到正确链路；
**认不出时返回结构化选项让用户选**（返回 `NEED_FILE_KIND` + options + 已看到的线索），
**不要猜**。

### 4.5 工具数量与可见性

- 数量本身不是问题，**歧义**才是（同问多路 → 加互斥指引；真冗余 → 标 DEPRECATED 或合并）
- **按身份裁剪 `tools/list`**：员工不该看到自己用不了的工具（减少白试与困惑）
  - 实现提示：MCP SDK 把 `tools/list` 放在**独立会话任务**里处理，中间件设的 contextvar
    **传不进去**（实测无效）；稳妥做法是**在 HTTP 层缓冲并裁剪响应体**（兼容 SSE 与 JSON）
  - 这是**双保险**：列表不给看 + 调用时仍强制拦

### 4.6 场景化设计规范（**最重要的一节**）

> 血泪教训：本系统第一版把管理端接口**一个 REST 端点做成一个工具**，结果 **49 个工具**。
> 生产审计实测：**只有 13 个被调用过**（27%）。用户原话：「40+ 个工具根本用不上那么多。
> 而且我们也不应该一个 restful api 就做一个工具。我们要根据实际场景来。」
> 重构后 **49 → 16**，且列表只暴露新集。
> ⚠️ **2026-10-06 更新：现在共 25 个** —— 结算域 16 个不变，**新增作业域 9 个**
> （`visit_my_tasks` / `visit_self_report` / `visit_team_tasks` / `visit_task_assign` /
> `visit_task_confirm` / `visit_task_report` / `visit_task_return` / `visit_task_board` /
> `visit_task_transfer` 队长间转队），并新增**队长档**（10 个 = 员工 6 + 本队 4）。

#### 4.6.1 核心原则

**一个工具 = 一个"用户会问的问题 / 会派的一件事"，而不是一次 API 调用。**

| 粒度 | 工具名长什么样 | 结果 |
|---|---|---|
| ❌ 接口粒度 | `store_skip_pair`、`rebuild_preview`、`recon_adjust_state`、`file_layout` | 模型要在 49 个里选，选错率高；用户看不懂 |
| ✅ 场景粒度 | `visit_upload`、`visit_overview`、`visit_payroll_export` | 名字即意图，参数少，描述可写"什么时候用我" |

#### 4.6.2 怎么找出场景（方法，不是拍脑袋）

1. **看审计数据**：`mcp_audit_log` 里**实际被调用过的工具**才是真需求（我们：13/49）
2. **走用户工作流**：把用户的一天/一个月要办的事列出来（导入→看数→发薪→对账→导出）
3. **按"问题类型"聚类**：同一类问题的多个端点 → 合成一个工具 + `view` 枚举
4. **一件事拆成多个工具 = 反模式**：例如"看数据"被拆成 `month_summary`/`dashboard`/
   `list_months`/`perf_ranking`/`person_detail` 五个；"导出"被拆成四个

#### 4.6.3 设计规则（逐条）

| # | 规则 | 说明 |
|---|---|---|
| 1 | **工具名用用户语言** | `visit_upload` 而不是 `visit_upload_recon`；内部实现细节不进名字 |
| 2 | **同域不同视角 → `view` 枚举** | 一次调用尽量答完整，减少往返；每个取值在描述里写清"回答什么问题" |
| 3 | **有副作用的操作 → `action` 枚举 + 闸门** | `action=merge\|split\|run` 等写值需确认语；只读 `view` 不需要 |
| 4 | **读写分离** | 只读工具与写工具**不混在一个工具里**（注解 `readOnlyHint` 与闸门依赖它） |
| 5 | **统一"预演"能力** | 危险/有副作用的操作提供 `dry_run` / `action=preview`：只报影响面、不落库 |
| 6 | **互斥指引必须写** | 描述里明确"看全公司某月→A；看某个人→B；看我自己的→C"——**调用成功率的最大杠杆** |
| 7 | **非法枚举值 → `BAD_PARAM` + 列出可选值** | 别让模型猜；报错即教学 |
| 8 | **不要过度合并** | 一个工具塞太多 `view` 会变成"上帝工具"，模型照样选错。目标是**场景数量 ≈ 10~16**，不是"最少工具数" |
| 9 | **默认拒绝** | 新增工具默认员工不可用（安全的失败方向），显式登记才开放 |

#### 4.6.4 合并映射表（示范：本系统 49 → 16）

| 场景工具 | 合并掉的接口粒度工具 |
|---|---|
| `visit_upload` 导入文件 | upload_file + upload_recon + finalize_file |
| `visit_overview` 看月度概况 | month_summary + dashboard + list_months + perf_ranking |
| `visit_person` 看某人 | month_salary + person_detail |
| `visit_payroll` 发薪与找平 | payroll_rows + settlement_trace + payroll_update |
| `visit_payroll_export` 导出发薪表 | export_salary + export_payroll_settle + payroll_generate + payroll_mark_paid |
| `visit_recon` 看对账 | recon_status + recon_diff + recon_interpret + recon_adjust + adjust_state + settlement |
| `visit_recon_export` 导出对账 | export_recon_report + export_recon_result + export_recon_diff |
| `visit_files` 文件与任务 | file_list + file_layout + file_report + list_tasks |
| `visit_rebuild` 重算 | rebuild_preview + rebuild_month |
| `visit_staff` 员工管理 | staff_list + staff_set_status |
| `visit_config` 配置 | config_get + config_set + set_per_point |
| `visit_store` 店铺主档 | store_search + merge_pair + skip_pair + apply_all + split_entity + ai_run（6→1） |
| `visit_verify` 数据体检 | verify_integrity |
| 员工侧 6 个 | `visit_whoami` / `visit_my_perf`（view=month\|daily）/ `visit_my_pay`（一次给全：进度+台账+轨迹） / **`visit_my_tasks`**（我的任务：今天派的 ∪ 没做完自动延续 + 今天已填点数） / **`visit_self_report`**（点数+进度**一次提交**） / **`visit_task_report`**（单条报进度，含"队长确认后当天锁住"校验） |
| **队长侧 3 个**（2026-10-06 新增档位） | **`visit_team_tasks`**（本队任务 + 待确认队列，tab 按"有没有分人"）/ **`visit_task_assign`**（派工 / 改派 / **回收**=person_codes 传 `[]`）/ **`visit_task_confirm`**（确认 / **一键全确认** all_today=True / 驳回 reject=True+pct） |
| **管理员侧新增 2 个** | **`visit_task_board`**（任务总表：tab 计数 + 任务行 + 按队汇总 + 停滞口径）/ **`visit_task_return`**（按线路**批量撤回**到车站池，只撤没分到人的） |

> **授权矩阵**（`mcp_service/authz.py`）：员工 6 / **队长 9** / 管理员 24。
> 队长档 = 员工档 + 3 个本队任务工具。作业域工具与 Web 端**走同一套服务层**
> （`app/services/bd_tasks.py` 等），口径不会漂移；能力层在 `mcp_service/task_ops.py`。

#### 4.6.5 实施要点（避免踩坑）

- **实现方式**：新工具是**薄封装**——内部调用**已有能力函数**，**绝不重写业务逻辑**
  （业务逻辑只有一份实现，是这类重构的底线）
- **删除旧名 vs 兼容期**：两种都可，但**列表只暴露新集**（用户与模型立刻看到干净的场景面）；
  兼容期只在"显式调用旧名"时生效，到期删除
- **改动面评估**：先数清"注册工具的文件数"与"测试里引用旧工具名的处数"
  （我们：11 个注册文件 + 约 66 处测试引用）——这决定工作量与回归风险
- **验收必须包含**：工具数正确 / **旧名确实消失**（或仍可调用）/ 按身份裁剪生效 /
  越权仍被拦 / 写闸门仍有效 / **关键业务数字与重构前逐项一致** / 自洽检查仍全过

---

## 5. 身份与权限

### 5.1 数据模型（最小集）

```
api_tokens(id, user_id, name, token_prefix, token_hash, scopes,
           created_at, last_used_at, revoked_at, expires_at)
```

- **绑定到人**（不是共享密钥）；库内只存 hash；`token_prefix` 用于快速定位
- 解析产物 `Actor{uid, role, scopes, token_id, person_code}` **贯穿每次调用**

### 5.2 授权矩阵：**默认拒绝**

- 每个工具显式登记"所需角色/权限"；**未登记 = 仅管理员**
- 员工可用面用**白名单**（新增工具默认员工不可用 → 安全的失败方向）
- 越权 → `FORBIDDEN_TOOL`（结构化、`retryable=false`、hint 说明）
- **顺序**：授权必须在**参数校验之前**（否则未授权者可探测参数）

### 5.3 状态联动

```
token 有效 ⟺ 未吊销 ∧ 未过期 ∧ 绑定用户 is_active ∧ status == "active"
```

- **每次请求都校验**（不是只在连接时）→ 用户请假/停用后**下一个调用就失败**
- 失效原因可区分（revoked / expired / user_inactive / user_status:leave）→ 便于排查
- 授权环节同样要校验（见 §3.3 坑 5）

### 5.4 审计：两阶段写入

- 先插一行（`ok=NULL`）→ 执行后回填 `ok`/`error_code`/`detail`/耗时
- **被拒绝的调用也要留痕**（"谁试图越权"最该记）
- 参数脱敏（base64 只记长度、路径只记 basename），**绝不记 token 明文**
- 审计写入失败**不能**影响工具返回（best-effort）
- 坑：从调用栈推断"当前工具名"时**层级数要对**（否则记成包装函数名，审计失去追溯力）——
  稳妥做法是**向上找最近的 `visit_*` 函数**

### 5.5 用户自助

- `/my/token`：生成（明文只显示一次）/ 查看前缀与最后使用 / 吊销
- 管理员可代发/吊销；员工状态变更后页面提示"其凭据已随之失效"
- 有 OAuth 后，自助签发作为**兜底**（无浏览器/自动化场景）

---

## 6. 部署与发布

### 6.1 部署形态

- **同一镜像**（web 与 mcp 共用），**不同入口命令**；迁移（alembic）只由 web 容器跑
- MCP 服务只暴露本机端口，由系统 Nginx 反代 `/mcp`
- OAuth 的 `/.well-known/*`、`/oauth/*` 反代到 web

### 6.2 环境变量清单（模板）

```
DATABASE_URL=<与 web 同库>
VISIT_MCP_HOST=0.0.0.0            # 容器内
VISIT_MCP_PORT=8765
VISIT_MCP_PUBLIC_HOST=<域名>       # 反代域名（Host 校验白名单）
VISIT_MCP_STATIC_TOKENS=0         # 生产只用库内 token
VISIT_MCP_TOKEN=<占位随机串>       # 静态 token 关闭后仅满足启动校验
VISIT_OAUTH_ISSUER=https://<域名>       # 授权服务器（web）
VISIT_OAUTH_RESOURCE=https://<域名>     # 受保护资源（MCP）；须与客户端 URL 同源
VISIT_OAUTH_ENABLED=1
VISIT_OAUTH_ACCESS_HOURS=24
VISIT_OAUTH_REFRESH_DAYS=90
```

### 6.3 上线步骤（含数据迁移）

1. **备份**（pg_dump）
2. 补 `.env` 新增项 → 发布代码（entrypoint 自动 `alembic upgrade`）
3. **回填新表数据**（若新增了物化表）：**不重导历史数据**，用现有数据生成新表；
   脚本**幂等**（先清后建）、**只写新表**、末尾跑自洽检查
4. 起 MCP 服务 → 配 Nginx → 重载
5. **验收**（见 §7）
6. 回滚：代码回退 + `alembic downgrade`（老表零改动）

### 6.4 迁移的坑

- **`alembic heads` 必须只有一个**：新迁移的 revision id **不要与既有撞车**（撞了会
  报 CycleDetected，**线上 upgrade 直接失败**）。加迁移后**立刻跑 `alembic heads` 验证**。
- 本地库若是早期 `create_all` 建的，**后续迁移新增的列不会自动补** → 运行时报
  `no such column`；准备一个"补列"脚本（只 `ADD COLUMN`，不改数据）。

---

## 7. 验收清单（照着跑）

### 7.1 连通性（P0 级）

- [ ] 客户端能连上，`initialize` 成功，协商到预期协议版本
- [ ] 工具清单可见且数量符合预期
- [ ] 鉴权失败返回结构化 `UNAUTHORIZED`（不是裸异常）
- [ ] 经**反代域名**（HTTPS）也能连通（验证 Host 白名单）

### 7.2 业务正确性

- [ ] 用**真实文件**跑通主链路，产出与既有系统/基准**逐项一致**
- [ ] 与生产**逐人对比**（能取到就取），差异必须能解释
- [ ] **数据自洽检查**全过（内部不变量：金额/状态/来源可反查）

### 7.3 身份与权限

- [ ] 员工 token：能看到/只能用"我的"类工具；调管理员工具 → `FORBIDDEN_TOOL`（**无副作用**）
- [ ] 员工 token 传他人的标识 → 拒绝（不是静默过滤）
- [ ] 员工改"请假" → token **立即失效**；改回 → 恢复
- [ ] 吊销 / 过期 → 失效
- [ ] `tools/list` 按身份裁剪（员工看不到用不了的工具）
- [ ] 审计：成功与被拒都有记录，含 `user_id`/工具/结果

### 7.4 OAuth

- [ ] 完整流程：发现 → 注册 → 授权（浏览器登录）→ 换 token → **用换出的 token 调工具成功**
- [ ] PKCE 校验失败 / code 重放（**吊销已换出 token**）/ redirect_uri 不匹配 → 均正确拒绝
- [ ] 员工申请 write → 实际只拿到 read
- [ ] refresh 轮换（旧 refresh 失效）
- [ ] 非在岗账号 → 授权被拒（明确文案）

---

## 8. 工程实践（避免"人工介入"）

| 坑 | 做法 |
|---|---|
| 改完代码不生效 | **Python 不热加载** → 写"重启+验证"脚本（重启后核对工具清单与自洽检查）；客户端需**重连**才刷新工具 |
| 重启误杀客户端 | 只杀**监听**进程（`lsof -ti:PORT -sTCP:LISTEN`），否则会杀掉连着该端口的客户端 |
| 端口开放≠就绪 | 就绪判断用**协议握手探测**（curl initialize 200），不是只看端口 |
| 静默数据错 | 关键自动化要**幂等 + best-effort + 自检**；无法自检的改动不做 |
| 测试污染生产目录 | 测试 fixture **重定向上传/导出目录**到临时目录（否则真实目录会被塞满垃圾文件） |
| 大任务做不完 | 拆小批次、**测试优先**（先跑通一条再扩展），别攒到最后一起测 |

---

## 9. 复用检查表（新系统接入时按顺序打勾）

1. [ ] 确定**架构**（独立进程 + 复用业务服务层 + 独立 venv/镜像）
2. [ ] 打通**连通性**（HTTP + stdio 两种模式都试；记录协议版本与会话行为）
3. [ ] 设计**工具清单**：**先定场景再定工具**（§4.6）——一个工具=一个用户问题/一件事；
       同域多视角用 `view` 枚举；读写分离；统一 `dry_run`；写互斥指引（别按 REST 端点 1:1 映射）
4. [ ] 定**错误信封**（含 `retryable`）与**错误码表**
5. [ ] 定**授权矩阵**（默认拒绝 + 白名单 + 员工/管理员）
6. [ ] 实现**身份**（token 绑定人 + 状态联动 + 两阶段审计 + 自助签发页）
7. [ ] 实现 **OAuth/SSO**（6 端点 + PKCE + 轮换 + scope 降级）
8. [ ] **按身份裁剪 tools/list**（HTTP 层裁剪响应体）
9. [ ] **部署**（同镜像独立服务 + SSE 友好反代 + 环境变量清单）
10. [ ] **迁移/回填**（幂等、只写新表、不重导历史）+ 验证 `alembic heads` 唯一
11. [ ] **验收**（§7 全部清单）
12. [ ] **文档**（本手册 + 本系统的实现索引）

---

## 附：本系统的实现索引（参考实现）

| 主题 | 文件 |
|---|---|
| MCP 服务（进程/传输/反代） | `mcp_service/server.py`、`mcp_service/run.sh`、`deploy/compose.yaml`、`deploy/nginx.store-settle.conf` |
| 鉴权与授权 | `mcp_service/auth.py`、`mcp_service/authz.py`、`mcp_service/tokens.py` |
| 审计 | `mcp_service/audit.py` |
| 作业域字段 | `bd_task.store_count`（店铺数）：队员报到 **100% 时必填**（允许 0），队长批量补录可留空 —— `visit_task_report(store_count=…)` |
| 工具实现 | **`mcp_service/scenario_ops.py`（当前 25 个工具的注册唯一入口）**、`mcp_service/task_ops.py`（作业域能力层）；历史模块 `read_ops.py`、`my_ops.py`、`write_ops.py`、`recon_ops.py`、`recon_write_ops.py`、`payroll_write_ops.py`、`store_write_ops.py`、`export_ops.py`、`misc_ops.py` |
| OAuth/SSO | `app/routers/oauth_r.py`、`app/services/oauth.py`、`app/templates/oauth_consent.html` |
| 自助签发 / 审计页 | `app/routers/tokens_r.py`、`app/services/mcp_tokens.py`、`app/templates/my_token.html` |
| 运维脚本 | `scripts/mcp_restart.sh`、`scripts/verify_payroll_logic.py`、`scripts/compare_with_prod.py`、`scripts/backfill_prod_new_tables.py` |
| 规格 | `docs/specs-mcp-identity.md`、`docs/specs-mcp-oauth.md`、`docs/specs-mcp-tools-scenario.md`（工具集设计稿）、`docs/记录-作业域.md`（作业域工具实施记录） |
