# MCP、OAuth 与连接器 Skill 恢复补充计划

更新：2026-11-13（Asia/Singapore）。状态：**M0 完成；M1 源码与隔离验收完成；M2 查询分支完成；M3 工具源码与定向验收完成（27 个工具）；M4 源码及隔离浏览器验收完成；M5 连接器/Skill源码包及官方SDK通过（WorkBuddy实机待验收）；M6 全量检查及隔离Docker/回退通过（WorkBuddy与默认入口窄范围发布待完成）**。

本计划承接 [React/NestJS 重构与部署计划](计划-React-NestJS重构与部署.md)，按用户本次要求，恢复此前跳过的 MCP、OAuth、Token 管理及 WorkBuddy 连接器 Skill。仅覆盖这些事项，不恢复算法生成、性能压测等其他已排除范围，也不追溯改变已上线版本的验收结论。本次只登记计划，未实施功能、部署或访问生产。

## 1. 目标、范围与完成标准

目标：用户能通过连接器登录当前巡店系统，由 AI 按账号权限查询及执行业务；新实现复用 NestJS 服务层，保留旧工具契约、业务口径及历史数据。

最小改动范围：`apps/server` 的 MCP/OAuth/凭据适配、必要的管理端凭据与授权界面、`deploy-next` 网关与发布配置、当前连接器包及对应测试。员工端仅在授权登录跳转确有需要时调整；不重写已迁移业务，不重新运行旧 Python MCP。

完成条件：

- 27 个旧工具逐项核对参数、输出、可见性、权限、错误和副作用，并通过受影响场景验收；历史记录不直接视为当前实现完成。
- OAuth 连接、登录、授权、刷新、撤销及账号失效闭环通过；Token 管理可用，历史有效凭据兼容策略有明确证据。
- 连接器与 Skill 同步实际工具和权限，目标客户端完整连接通过。
- 本地 Docker 网关、SDK、授权页面及业务闭环验收通过；公开业务数据、凭据与私有文件不泄漏。
- 源码验收、本地发布、生产发布分别记录。生产发布单独授权；未完成生产验收时，状态不得写成“已上线”。

## 2. 当前事实与差异

- 新栈 MCP 源码、SDK 依赖及网关转发在 2026-11-11 撤回，公开路径按排除策略返回 404。旧实现和历史表保留，当前不能按早期“已迁移部分工具”记录续勾完成。
- 旧注册入口为 [scenario_ops.py](../legacy/mcp_service/scenario_ops.py)，实际注册 **27 个**工具；[授权矩阵](../legacy/mcp_service/authz.py)规定员工 6 个、队长 10 个、管理员全部 27 个。
- 旧 [连接器 Skill](../legacy/deploy/connector/visit-settle/skills/visit-settle/SKILL.md)同时出现“25 个工具”和“16 个速查”的陈旧说明，恢复时必须修正。
- OAuth、Token 与审计参考分别位于 `legacy/app/routers/oauth_r.py`、`legacy/app/services/oauth.py`、`legacy/app/routers/tokens_r.py`、`legacy/app/services/mcp_tokens.py`、`legacy/mcp_service/tokens.py` 和 `legacy/mcp_service/audit.py`。
- [MCP 对接手册](MCP对接手册.md)作为历史协议和业务参考；工具及角色以注册代码、中央授权和有效测试交叉核对。旧文档或注释中的计数、默认权限不能直接替代核验。

## 3. 执行队列

以下按依赖执行，最新完成状态见文首及执行结果。M0 确定契约后再写接线，M1 建立身份基础，M2/M3 恢复业务，M4/M5 完成免手工 Token 的客户端连接，M6 收口本地验收。每阶段只更新对应完成项和证据，不新增独立过程文档。

| 队列 | 交付内容 | 依赖 | 验收门槛 |
|---|---|---|---|
| M0 | 契约、数据与兼容基线 | 无 | 27 项工具清单、角色矩阵、历史凭据/表结构和新服务映射明确 |
| M1 | MCP 协议、Token 身份及审计 | M0 | 官方 SDK 连接，身份裁剪、失效和拒绝路径正确 |
| M2 | 查询工具 | M1 | 只读结果、本人/本队边界及业务口径对齐 |
| M3 | 写入、导入导出及作业工具 | M1、对应 M2 查询 | 预演、确认、封账、冲突、重试及副作用通过 |
| M4 | OAuth 与凭据管理界面 | M1；M0 明确登录与权限模型 | 登录授权、刷新、撤销及账号状态闭环通过 |
| M5 | WorkBuddy 连接器及 Skill | M2、M3、M4 | 27 项说明与实现一致，目标客户端连接及代表流程通过 |
| M6 | 本地 Docker 验收及发布准备 | M1–M5 | 适用检查通过、本地路径与回滚可用，待生产发布授权 |

### M0：契约与数据兼容基线

- [x] 对照旧注册入口、schema、授权矩阵与测试，为下面 27 个工具逐项登记新服务落点及差异；保留工具名，避免另造工资、找平或作业口径。
- [x] 确认 Token、OAuth、审计的实际模型、列及历史格式；优先复用现有结构、ID和数据，禁止为恢复功能清表或重签所有凭据。若发现结构确需变化，先列出具体缺口与兼容方案，不自行迁移。
- [x] 核对当前账号停用、请假、离职、逻辑删除、解绑及团队角色规则；`users.role` 与团队身份分别解析，旧 Token 不得绕过新系统状态检查。
- [x] 明确旧 Token 的前缀、摘要、过期/撤销与 scope 兼容、旧客户端参数及错误契约；旧临时静态 Token、无身份 stdio 放行等行为单独核定，不默认恢复宽松权限。
- [x] 明确 `/mcp`、发现接口、OAuth 路径、issuer/resource、登录回跳与本地/生产域名配置契约，不将凭据写入文档。

### M1：MCP 协议、身份与审计

- [x] 在 NestJS 接入 MCP SDK、Streamable HTTP 和 `visit_whoami`，核对工具发现、调用与会话行为；旧 stdio 模式的保留方式在 M0 明确。
- [x] 接入兼容 Token 校验、read/write scope、过期/撤销、实时账号状态、本人绑定及队长本队授权；发现列表和实际调用双重限制，默认拒绝未登记能力。
- [x] 恢复统一错误信封、`retryable` 语义及两阶段审计；审计内容脱敏，不记录 Token、原件或个人工资明细。
- [x] 使用既有测试设施及官方 SDK 验证连接、身份、角色发现、越权和失效路径。

### M2/M3：27 项业务工具恢复清单

每一项验收同时覆盖工具列表可见性与实际调用授权；兼有查询/写入分支的工具分别验收。

| 状态 | 工具 | 恢复内容及关键边界 |
|---|---|---|
| [x] | `visit_whoami` | 当前身份、绑定、角色；随 M1 验收 |
| [x] | `visit_my_perf` | 本人月绩效与逐日明细，禁止查询他人 |
| [x] | `visit_my_pay` | 本人找平、发放与核销轨迹，按月历史过滤 |
| [x] | `visit_my_tasks` | 本人任务及身份过滤 |
| [x] | `visit_self_report` | 本人每日自报，沿用当前业务校验 |
| [x] | `visit_task_report` | 本人任务进展、完成条件及店铺数规则 |
| [x] | `visit_team_tasks` | 队长本队任务，禁止跨队读取 |
| [x] | `visit_task_assign` | 本队派工、成员资格与冲突保护 |
| [x] | `visit_task_transfer` | 转队规则、来源权限和目标资格 |
| [x] | `visit_task_confirm` | 本队进展确认、版本与重复提交保护 |
| [x] | `visit_task_return` | 按旧矩阵的管理员回收权限及业务状态 |
| [x] | `visit_task_board` | 按旧矩阵的管理员看板与筛选 |
| [x] | `visit_day_report` | 管理员指定员工日自报与进展调整 |
| [x] | `visit_upload` | 文件识别、预演、导入与私有文件访问 |
| [x] | `visit_task_import` | 车站任务导入、预览与确认 |
| [x] | `visit_overview` | 公司汇总、月份与排行视图 |
| [x] | `visit_person` | 管理员查员工绩效，编号/姓名与月份契约 |
| [x] | `visit_payroll` | 两期应付、找平状态及双向轨迹 |
| [x] | `visit_payroll_export` | 预演不登记；正式导出登记发放及抵扣找平 |
| [x] | `visit_recon` | 对账状态、差异及已有 AI 解读 |
| [x] | `visit_recon_export` | 对账文件类型、授权下载与私有性 |
| [x] | `visit_files` | 文件、布局、报告及任务查询 |
| [x] | `visit_rebuild` | 月重算预演/执行、确认语、封账及历史保护 |
| [x] | `visit_staff` | 账号查询与状态变更，逻辑删除账号不可重新启用 |
| [x] | `visit_config` | 配置查询/修改、确认及现有校验 |
| [x] | `visit_store` | 门店查询、配对、合并/拆分/应用/跳过，已发薪快照保护 |
| [x] | `visit_verify` | 旧 A1–A9 九项数据自洽检查与新服务口径对齐 |

- [ ] 查询工具优先复用已迁移服务；未知值不伪造为 0，不读真实 `raw_records` 隐私数据。
- [ ] 写工具复用业务层权限、事务、版本/幂等、封账与快照保护；浏览器凭据管理及授权提交保留 CSRF，MCP 调用使用适配后的明确凭据鉴权边界。
- [ ] 找平区分未回收余额和本月应扣补；两期应付沿用 `pay_plan`，不覆盖实发台账。不能把导出发薪当作无副作用查询。
- [ ] 不将旧任务工具擅自扩展为新批次能力；如当前服务映射有语义差异，在对应工具完成前明确解决并验证。

### M4：OAuth 与凭据管理

- [x] 恢复受保护资源/授权服务器发现、动态公共客户端注册、授权页、授权码交换及 PKCE S256。
- [x] 复用当前登录与角色体系，校验 redirect URI、state 的往返契约、scope 上限及授权确认，避免客户端跳转丢失。
- [x] 验证授权码一次性、有效期、重放处理、刷新轮换、撤销和账号状态失效；持久化只保存必要摘要。
- [x] 管理端补齐员工 Token 签发/撤销所需入口，明文只在签发响应展示一次；沿用 Appica 和当前交互规范。
- [x] 核对历史 OAuth 客户端/凭据与当前域名兼容；确需重新连接时明确原因及用户动作，不承诺所有旧会话无条件沿用。

### M5：连接器与 Skill

- [x] 在当前发布目录维护连接器包：`mcp.json`、`oauth-info.json`、元信息、图标及 `skills/visit-settle/SKILL.md`；旧包保留为历史参考。
- [x] Skill 更新为实际 27 个工具及员工 6/队长 10/管理员 27 的矩阵，补齐作业工具及参数说明，消除旧的 16/25 计数。
- [x] 保留日元、找平正负、已物化工资、先查后写、预演/确认、导出登记和未知月份询问规则；不在 Skill 中复制业务计算逻辑或保存 Token。
- [ ] 使用目标客户端完成 OAuth 连接、工具发现、本人查询和角色拒绝验证；写入代表流程在隔离环境验收。没有客户端证据时明确标为待验收。


### M6：验证、本地发布与生产准备

- [x] 复用既有测试和 `scripts/test-isolated.mjs`；写入 fixture 仅在随机 schema-only 临时库。优先 mocks，真实供应商调用仅用于必要行为确认。
- [x] 代码完成后依次运行适用测试、受影响模块 typecheck 和 build；仅有文档改动时检查 diff、内部链接、工具清单及状态一致性。
- [x] 授权界面及凭据管理有交互变更时，使用对应隔离浏览器分组一起验证关键流程；已有开发服务优先，必要临时进程用后清理。
- [x] 进入本地发布时，按实际源码差异确认 API、Gateway、管理端及 Worker 的影响；仅重建必要产物，不默认全栈更新。
- [x] 将当前 MCP/OAuth 路径 404 的排除规则替换为正确代理并同步回归断言，验证发现/协议/授权路径不被 SPA 吞掉，未知静态路径仍正确返回 404。
- [x] 独立本地 Docker 候选的官方 SDK/OAuth/浏览器、私有下载/重启、Worker和回退/重新部署验收通过；回退恢复404作为明确能力降级记录。
- [ ] 目标 WorkBuddy 实机导入与 Skill 加载；默认8000如需更新，须先形成排除既有其他改动的窄范围发布候选，不能直接使用acceptanceOnly快照切流。
- [x] 汇报源码、本地和待生产状态；如后续获准生产发布，遵循 [生产部署与回滚手册](../deploy-next/生产部署与回滚.md)，另核验公网发现地址、OAuth 回调和客户端连接。

## 4. 风险与停止条件

关键风险为历史 Token/角色兼容、OAuth 域名与回调、写工具的发薪/重算副作用，以及旧任务和当前作业服务的语义差异。任何权限越界、未解释财务差异、业务保护缺失或凭据泄漏均阻塞对应阶段交付。

本次计划登记完成后停止，不据此启动旧服务、变更数据库或部署。后续实施以本队列为准，满足阶段验收即停止重复验证；生产发布仍需明确授权。

## 5. M0 执行结果（2026-11-13）

已生成 [27 项契约与服务候选映射](refactor/mcp-legacy-contracts.json)，含旧参数/默认值、角色、描述、源码位置及新服务候选。候选路径和声明已核对，不代表响应、事务和语义已迁移。`visit_verify` 无直接聚合服务；本人核销轨迹、文件列表/布局/任务、员工状态专用更新和导入预演仍需要适配。任务派发/转队/回收需要核对当前版本契约。对账导出逻辑目前位于 Controller，需要提取共享业务逻辑后供 MCP 复用。

身份与数据基线：

- `api_tokens` 使用明文前 8 字符索引、整串 SHA256 十六进制摘要；保留用户绑定、scope、过期/撤销和历史 ID。无需因迁移统一重签，但尚未证明当前实现兼容真实凭据。
- `mcp_audit_log` 沿用两阶段 `ok=NULL → 成功/失败`；参数脱敏，不记录完整工资结果、原件或 Token。
- OAuth 现有表为 `oauth_clients`、`oauth_codes`、`oauth_refresh_tokens`；授权码关联 access token 以处理重放，刷新沿用摘要与撤销字段。本阶段只核对源码模型，实际库结构仍需隔离 schema 验证。
- 旧静态测试 Token 绕过账号状态，旧无身份 stdio 放行读取。新恢复默认仅接受绑定用户的数据库 Token；本地 stdio 如需保留也使用同一绑定凭据。账号逻辑删除、解绑及团队身份随当前系统规则实时检查。
- 员工 6、队长 10、管理员 27 的发现与调用权限保持；端角色与团队身份分别解析，离队失效及事务内身份复核在 M1/M2 验收。
- 新 API 有全局 `/api` 前缀，公开 `/mcp`、`/.well-known/*`、`/oauth/*` 需要显式适配；浏览器 Session 不能代替 MCP Token。错误信封为 `{ok,data}` / `{ok:false,error:{code,message,retryable,hint}}`；公开 resource 通过 `NEXT_MCP_PUBLIC_URL` 明确配置，生产要求 HTTPS；issuer 和授权登录回跳接线排入 M4。

一键开发已有 `pnpm dev`：`scripts/dev.mjs` 启动 Vite 管理端 8001、员工端 8002、NestJS API 和 Worker watch；统一配置入口为 `scripts/dev-lib.mjs`。本阶段定向验证现有加载器，不重复实现；确认旧启动器进程已退出且开发端口空闲后，已通过 `pnpm dev` 启动四进程热更新，不打包 Docker。

M0 补核完成：当前任务服务分别读取 `users.role` 和有效的 `bd_team_member` 关系，不能仅凭成员角色升级端权限。浏览器登录允许请假，旧 MCP 凭据则要求 active；本次 MCP 保留更严格的 active 要求，并沿用新系统首次改密限制。网关排除规则来自 `scripts/release-lib.mjs`，在 M6 与验收断言一起调整。剩余为 M2–M6；当前不改变现有业务数据或生产状态。

## 6. M1 源码与隔离验收（2026-11-13）

已在 `apps/server/src/mcp.ts` 接入官方 TypeScript SDK，提供 API 原生 `/mcp` Streamable HTTP POST、资源发现和 `visit_whoami`。采用无状态传输，每次请求独立身份与 Server，避免跨 Token 共享会话；GET/DELETE 返回 405。当前工具列表只公布已完成的身份工具，其余 26 项不冒充可用。

数据库 Token 沿用前 8 字符索引及 SHA256，拒绝过期、撤销、请假、停用、离职、删除、未完成首次改密及非法 scope；不接受旧静态测试 Token 或无身份绕过。身份工具保持旧输出字段和口径，事务内重新检查绑定、角色和 scope。两阶段审计仅保存空参数形状及结果状态，不保存任意输入或身份明细；使用量更新失败不改变已成功的只读结果。旧能力说明文字后续随 M2–M5 工具恢复统一核对。

验证结果：

- 官方 SDK、无状态身份隔离、旧信封、角色 6/10 边界、撤销/状态/过期、同前缀伪造、域来源拒绝、事务前绑定变更、错误脱敏及审计失败路径通过。
- MCP 定向测试 4 项通过，0 跳过，含真实 schema-only PostgreSQL 的 Token、UTC 过期、绑定变更、审计和撤销。隔离运行器证据 `data/refactor/mcp-auth-acceptance.json`：基准不变、临时库已删除。
- 开发加载器原有 2 项测试、隔离运行器测试（含新增受限文件选择）通过；server typecheck 与 build 通过。类型声明修复后只补跑失败的类型检查和后续构建，行为测试未重复运行。
- 隔离运行器支持 `node scripts/test-isolated.mjs --integration --file=apps/server/test/mcp.test.ts`，只接受该套件发现的测试路径；不为日常单模块验证运行全量测试。

开发命令仍为 `pnpm dev`；管理端 `http://localhost:8001/admin/`、员工端 `http://localhost:8002/`、API 默认 3000。MCP 在 API 的 `http://localhost:3000/mcp`，实际公开地址可由 `NEXT_MCP_PUBLIC_URL` 配置。该入口需要已有有效 Token，本轮未对真实账号签发或使用凭据。临时 SDK 服务器已关闭。随后确认旧开发启动器 PID 已退出、3000/8001/8002 无监听，已用 `pnpm dev` 启动日常四进程开发服务并保留运行。两端入口及 API ready 均为 200，资源发现为 200、无凭据 MCP 为 401；未使用真实 Token 调业务工具。

状态：**源码及隔离验收完成，未更新 Docker，未操作生产**。Docker 的 MCP/OAuth 404 排除规则仍保留至 M6；OAuth 尚未恢复，资源发现不宣称已有授权服务器。

剩余队列：M2 的 26 项工具中查询分支 → M3 写入/作业/导入导出 → M4 OAuth/Token 管理 → M5 连接器/Skill → M6 本地 Docker 和客户端阶段验收；生产发布另行授权。

## 7. M2 查询首批源码验收（2026-11-13）

本批新增 `visit_my_perf`、`visit_my_pay`、`visit_overview`、`visit_person`，当前共公布 5 个工具。查询位于 `apps/server/src/mcp-read.ts`，由公共 MCP 入口负责实时 Token 校验、事务内身份复核、角色裁剪及脱敏审计；所有业务读取在 REPEATABLE READ / READ ONLY 事务内完成。

业务口径：

- 本人绩效读取 `month_perf_records` 和 `person_daily_stats`，保留月汇总/逐日差异、最新月选择及员工可见起始月规则；未物化工资为 null，工资不按当前配置重算。
- 本人找平读取 `payroll_adjusts`、`payroll_payments`、`payroll_settlement_links`，按月过滤保留旧双向轨迹及嵌套历史信息；不将未回收余额当成本月应扣补，不执行登记或核销。查询 SQL 数量不随台账行数增长。
- 公司总览分别读取正式表统计与已物化工资；点数不为 1/2 的正式记录仍按旧总点数统计。排行保留点数排序、数量上限和物化工资。
- 对账标记沿用“最新未被替代的月度任务具有数据”的旧口径，空任务不因存在而标为有对账；月份列表保留正式表/月绩效/有效对账三类计数。
- 管理员查员工支持编号精确、姓名包含和未指定月份时的系统最新月；不套用员工端月份隐藏。员工/队长在参数校验及查询之前拒绝管理员工具，本人工具显式指定他人也先拒绝。

验证：本人查询 85 组、管理员查询 65 组，共 **150 组**结果与隔离库中的旧 Python 完整输出一致。旧函数正文通过 AST 加载，绕开已淘汰 Python MCP 传输依赖；仅用于验收，运行时不依赖 Python。Oracle 强制连接随机 schema-only 临时库并开启数据库只读，未读取真实 raw_records；管理员总览的来源行均是临时库合成 fixture。

本人查询测试 2 项、管理员查询测试 2 项和受公共入口改动影响的身份回归 4 项通过，零跳过；财务/统计表查询前后指纹不变，隔离运行器基准指纹不变且临时库已删除。server typecheck/build 通过。管理员新增后只跑新增管理员测试及相应静态检查，未重复已通过的 85 组本人对照。证据：`data/refactor/mcp-read-acceptance.json`。

剩余：M2 的本人/本队任务、任务看板、薪资/对账、文件、员工/配置/门店查询及自洽检查；M3 全部写入分支、M4 OAuth/凭据管理、M5 连接器/Skill、M6 Docker及目标客户端验收。仅上述 5 个完整工具勾选完成，其他 22 项仍逐项在队列。**源码与隔离验收完成；未更新 Docker，未操作生产。**

开发入口核对：当前 API ready 与资源发现为 200，无凭据 MCP 为 401；沿用 `pnpm dev`，管理端 8001、员工端 8002、API 3000。

## 8. M2 任务查询源码验收（2026-11-13）

新增 `visit_my_tasks`、`visit_team_tasks`、`visit_task_board`，当前合计 **8/27** 个完整工具源码验收通过。实现位于 `apps/server/src/mcp-task-read.ts`；公共 MCP 入口继续复核 Token/账号及绑定，业务读取处于只读一致性事务中。

- 本人任务按当前有效团队关系和本人担当先限定 SQL 范围，返回未完成任务、今日完成数、派工/延续标记及今日自报；失效的跨队担当记录不能恢复读取权限。
- 队长工具要求账号端角色、当前生效且未结束的队长成员关系、活跃团队和 `task.view_team` 能力；离队、未来生效、角色降级或能力关闭均拒绝。管理员沿用旧工具的全部队口径。
- 队长待派/进行中按有没有担当判断，计数和待确认队列不受当前关键词/tab 影响。待确认以最新一条进展为准，历史 pending 不覆盖最新已确认记录。
- 管理员的未分配计数来自活跃物理车站池，含未建任务的车站；已派队及完成数量按任务统计。复用现有中日文线路别名函数，保留跨线物理站、旧 station 关联、关键词、分页、按队汇总和停滞字段。
- 保留两处旧工具返回行为：恰好两天的进展计为行/统计停滞，但 `stale_only` 预筛选仍排除截止日；越界 page 的行数据夹到末页，返回 page 字段保留传入值。两者已纳入对照，不将其描述为新修复；若后续调整需单独统一相应契约。

验证：**90 组**隔离库完整输出与旧任务实现一致，覆盖三种角色范围、任务状态、最新审核、派工日期、跨线/CJK 搜索、空结果、停滞及越界分页；另验证跨队旧担当、离队、未来成员、端角色降级和角色能力关闭。两项任务测试及四项身份回归通过，具名测试选择器检查通过；带关键词的队长调用无论有/无结果均为 5 条事务内 SQL（含事务设置与身份复核），不逐行查询权限。任务/派工/进展/日报指纹不变，隔离运行器基准不变、临时库已删除；server typecheck/build 通过。证据：`data/refactor/mcp-task-read-acceptance.json`。

测试夹具按现有“一人一条现役团队关系”和角色能力更新时间约束构造；未改数据库结构。失败后只重跑具名集成用例，已通过的参数校验及上一批 150 组财务对照没有重复执行。运行器可用 `--file=apps/server/test/mcp-task-read.test.ts --name=schema-only SDK` 精确选择已有套件中的用例（命令行中需为含空格的 name 参数加引号），不能用于浏览器或任意文件。

剩余：M2 的薪资/对账、文件、员工/配置/门店查询和自洽检查；M3 写入与导入导出、M4 OAuth/凭据界面、M5 连接器/Skill、M6 Docker及目标客户端验收。其余 **19** 个完整工具仍待逐项恢复。**本批为源码与隔离验收，未更新 Docker、未操作生产。**

开发入口：现有热更新 API ready/资源发现为 200，无凭据 MCP 为 401；沿用 `pnpm dev`，管理端 8001、员工端 8002、API 3000。

## 9. M2 薪资与对账查询源码验收（2026-11-13）

新增 `visit_payroll` 和 `visit_recon`，当前共 **10/27** 个完整工具源码及隔离验收通过。实现位于 `apps/server/src/mcp-finance-read.ts`，沿用公共 Token、管理员授权、只读一致性事务和脱敏审计。员工/队长在参数检查及业务 SQL 前拒绝，工具列表不向其公布这两项能力。

- `visit_payroll(rows)` 保留分期快照的点数、店数、金额和奖金，读取发放台账判断两期已发；结转复用现有 `PayrollService.carry`，增加可传入当前事务客户端的内部参数，默认 API 调用不变。未回收余额与本月已实扣相加作为应扣补，不能因余额结清而丢掉本月已扣金额。既有 `pay_plan` 未改。
- `adjusts` 展示已确认记录、月绩效偏差和分期偏差，优先以 `payroll_adjusts` 及核销链接展示结清状态；不存在该源月账表时保留旧链条回退，仅作历史追溯展示。正式发薪仍使用现有账表和发放服务。
- `trace` 展示源月找平和当月发放的双向链接，批量读取关联记录，避免旧实现逐笔 SQL。保留原始金额、已回收、余额、来源行和微秒时间戳。含事务设置与身份复核共 5 条 SQL，空/有数据均一致。
- `visit_recon(status/diff)` 保留历史版本、差异人数/金额、月与逐日差异、反向名单和报表汇总；按旧 MCP 的月物化点价及奖金时间表调用现有纯规则函数换算差额预览。预览不覆盖工资、已确认找平或实发台账。快照点数和当前正式表点数分别展示，缺少当前值保持 null。
- `interpret` 只读已有文本、模型和生成时间，不调用模型或创建后台任务。按月选择最新未被替代版本；显式 task_id 可追溯旧版本。
- 兼容新 Worker 的 `sys_only=[{code,name,points}]` 和旧 `sys_only=[code]`，统一投影为旧 MCP 人员列表；不把对象当作人员编号，也不改写历史任务摘要。

验证：**54 组**完整输出与旧只读实现一致，覆盖 7 个月份、三类薪资视图、对账列表/当前版本/历史版本/显式任务、空数据、奖金阈值、快照过期、微秒、旧链回退和跨月多期核销；另验证新 Worker 名单结构、无权限调用、未知任务和固定 SQL 条数。两项 MCP 财务测试、现有结转/pay_plan 回归一项、身份回归四项通过；server typecheck/build 通过。

所有业务 fixture 在随机 schema-only 临时库内构造；Oracle 强制数据库只读。查询前后财务/对账/正式表指纹一致，摘要未变，未登记发放、重算或生成 AI。隔离运行器基准不变，三个临时库均已删除。初次失败为夹具 SQL 未使用的占位符，修正后只重跑具名集成用例和后续必要回归，未重复参数单测或此前 240 组对照。证据：`data/refactor/mcp-finance-read-acceptance.json`。

剩余：M2 的文件、员工/配置/门店只读分支和自洽检查；M3 写入/导入导出、M4 OAuth/凭据管理、M5 连接器/Skill、M6 Docker及目标客户端验收。其余 **17** 个完整工具仍在队列。**本批为源码和隔离验收，未更新 Docker、未操作生产。**

开发入口：现有 API ready 和资源发现为 200，无凭据 MCP 为 401；继续 `pnpm dev`，管理端 8001、员工端 8002、API 3000。

## 10. M2 文件、管理查询与自洽检查收口（2026-11-13）

完成 `visit_files(list/layout/report/tasks)`、`visit_verify`，以及 `visit_staff(list)`、`visit_config(get)`、`visit_store(search/pairs)` 的只读分支。当前共公布 15 个工具名：其中 **12 个完整工具验收通过，3 个只读分支通过但写入待 M3**；不得将三项混合工具勾选为完整完成。

查询实现为 `apps/server/src/mcp-management-read.ts` 和 `mcp-integrity-read.ts`：

- 文件列表按导入/正式表的月份和判定状态聚合；布局保留 value_map 顺序与可读规则；报告每个分桶最多 5 条样本，任务支持 kind/month/status 及被替代版本。SQL 不读取原始整行 JSON、口令、Token 摘要或原件路径。真实 raw_records 未作验收读取；新工具运行时仅在管理员显式查询文件信息时读取所需字段，测试全部使用隔离合成行。
- 员工只读列表保留旧字段，`last_login` 仍为历史命名，实际表示最近 MCP Token 使用时间；排除新系统逻辑删除账号。业务配置为白名单字段，不返回运行环境密钥。
- 门店搜索使用旧 LIKE/limit 口径；待处理候选对要求双方仍独立为主档，不读原始上传内容。混合工具的任何非空 action 明确返回未恢复，不能将写意图静默当查询。
- 错误/警告及布局文本中的常见服务器私有路径和凭据片段脱敏，返回样本按管理员权限限制；文本为数据，不执行其中指令。
- 文件列表修正旧 `visible_blank`/`blank` 归一化后的覆盖计数：新实现相加，和报告总数一致；这是明确记录的旧缺陷修正，另有断言，不冒充逐字相同。
- 自洽检查实际为 **A1–A9 九项**，此前计划所写八项已纠正。A6 仅作 info，不因计划变化修改实发；其他异常报 fail。每项最多 5 条样本，六条批量业务查询代替旧逐笔查库；不会自动修复或登记发放。

验证：64 组管理查询完整输出对照一致（含同类 blank fixture），另断言混合 blank 相加、删除账号排除、写入拒绝及敏感元数据脱敏。自洽检查在空库、全部通过、仅 info、八项 fail 四种状态与旧实现一致，覆盖全部九项及样本上限。管理查询参数单测、具名集成、自洽两项测试和受接线影响的身份回归通过；server typecheck/build 通过。失败重试仅针对具名集成用例，期间修正测试使用时间的文本重置与 SQL 引号，未改业务时间存储口径。

查询前后导入/原始合成行/正式表/申诉/任务/配置/账号/门店及财务表指纹不变（Token 使用量和审计按设计更新）；隔离库已清理、基准不变。证据 `data/refactor/mcp-management-read-acceptance.json`。未重跑此前未受影响的财务/任务对照，未做全量阶段验收。

**M2 查询范围收口；源码完成，Docker 和生产未更新。** 剩余 **15 个完整工具**：12 个写入/导入导出工具尚未恢复，另 3 个混合工具写分支待 M3；随后 M4 OAuth/凭据管理、M5 连接器/Skill、M6 本地 Docker与目标客户端验收。生产不自动发布。

开发入口：现有 API ready、资源发现为 200，无凭据 MCP 为 401。继续 `pnpm dev`，管理端 8001、员工端 8002、API 3000。

## 11. M3 员工状态与配置写入源码验收（2026-11-13）

恢复 `visit_staff(action=set_status)` 与 `visit_config(action=set)`，当前 **14/27 个完整工具**通过源码和隔离验收；`visit_store` 仍仅只读，其写入及其他 12 个工具继续在 M3。

实现为 `apps/server/src/mcp-management-write.ts`，复用新系统员工状态与配置事务服务：

- MCP 写事务重新校验绑定 Token、当前账号、scope/角色并对 Token 和账号加共享行锁；只读凭据或非管理员先拒绝。已撤销/过期/账号失效不能借先前请求身份继续写入。
- 员工状态确认语为 `确认改状态 <username> <status>`，状态沿用 active/leave/disabled/resigned 和 is_active 联动；不可操作管理员或复活逻辑删除账号。可选 `expected_status` 验证旧状态，底层复用抽取的 `EmployeeService.setStatus`；浏览器入口继续保留原 CSRF、管理员权限和状态冲突校验。
- 配置确认语为 `确认修改配置`，未提供字段保留原值，复用 `ConfigurationService.saveInTransaction` 的参数校验、财务锁和版本冲突保护；不重算工资、不修改历史实发。业务值范围沿用当前新系统，月份不再容许旧实现的非法 13 月。
- 配置查询可选 `include_version=true` 获取 `expected={id,version}`，默认查询输出保留旧契约。写入可提供该版本，缺省时使用事务开始时读取的快照并在加财务锁后再次核对，避免检查后被覆盖。
- 成功审计回填与业务写入同一事务提交；回填失败时整个写入回滚，再登记失败审计。参数仍只存空白名单形状，避免记录确认语中的身份或任意凭据。两项混合工具的 SDK annotations 已标识为可写/可能改变数据，不能误标只读。

验证：MCP 写入两项测试，覆盖 scope/角色、确认语、四种状态及 Token 失效、删除拒绝、参数边界、版本冲突、撤销发生在请求鉴权之后、审计失败回滚和财务表不变；现有员工 API 一项、配置及封账/历史实发保护一项、身份回归四项通过。受本批查询/schema 接线影响的管理集成回归（64 组旧输出对照）通过。server typecheck/build 通过，未重跑未受影响的任务/对账/工资整套对照。隔离运行器基准不变，各次临时库均清理。证据：`data/refactor/mcp-management-write-acceptance.json`。

状态：**源码和隔离验收完成，Docker 未更新，生产未操作**。本批没有在真实账号上改状态或修改业务配置，所有写入夹具均位于随机 schema-only 临时库。

剩余 **13 个完整工具**：门店合并/拆分/应用/跳过分支；员工自报/任务报工/指定员工日报、派工/转队/确认/回收；文件上传/任务导入；发薪导出、对账导出、月重算。之后仍为 M4 OAuth与Token管理、M5连接器/Skill、M6本地Docker及目标客户端验收。生产发布另行授权。

开发入口：继续 `pnpm dev`；当前 API ready/资源发现为200，无凭据MCP为401。管理端8001、员工端8002、API3000；混合写工具经MCP客户端调用，无新增前端页面。

## 12. M3 自报、任务报工及管理员日报源码验收（2026-11-13）

恢复 `visit_self_report`、`visit_task_report` 和 `visit_day_report(view/save)`，当前 **17/27 个完整工具**通过源码及隔离验收。实现位于 `apps/server/src/mcp-task-write.ts`；没有另建作业口径或写财务表。

- 将现有 `SelfReportService.view/submit` 的事务内逻辑提供为可复用方法，网页仍走原事务包装、CSRF和版本参数；MCP在其公共写事务里复用相同服务，点数、任务、日期计划联动与成功审计一并提交。
- 本人自报强制绑定身份、write scope，任务范围/团队成员/完成店数/当天审核状态继续由现有作业服务判断。未改变进度且无备注时跳过，不增加待确认进展；重复同内容自报保留单条人日记录及任务日进展。
- 单条任务报工复用 `StationTasksService.reportInTransaction`；队长可复核本队，员工仅本人担当，管理员沿用既有当天可调整条件。完成100%时员工必须填店数，0合法。保留已确认锁；adjusted 与 confirmed 的可重报规则不混为一谈。
- 管理员日报默认 action=view，read Token可调用；save需要write。无日报只允许补点数，已有日报仅能改符合审核条件的当天进展。日期默认 JST，工号优先精确匹配，姓名包含作为兜底。
- 保留旧MCP参数，新增可选日报 token 和 items.token / 任务 token；管理员查看可用 `include_version=true` 取得版本。缺省版本在持有作业锁的同一事务内取得，再由原服务复核。提供过期版本返回冲突，不覆盖新状态。

验证：MCP参数/权限单测和具名SDK集成通过，覆盖点数与进展原子回滚、空变更跳过、跨队、无write、完成店数、旧版本、确认锁、管理员补录限制、离队、失效账号及审计失败回滚；原自报、车站任务和身份相关回归通过，server typecheck/build通过。三组管理员日报只读输出（已有/补录/空）与旧Python一致。失败只重跑集成用例；确认锁夹具按旧业务规则修正，车站名投影修复后仅补跑受影响SDK及静态检查。

隔离运行器临时库已清理、基准不变；没有修改真实自报、任务或生产数据。证据：`data/refactor/mcp-task-write-acceptance.json`。源码与隔离验收完成，**Docker未更新，生产未操作**。

剩余 **10 个完整工具**：`visit_task_assign`、`visit_task_transfer`、`visit_task_confirm`、`visit_task_return`，`visit_store`写入分支，`visit_upload`、`visit_task_import`、`visit_payroll_export`、`visit_recon_export`、`visit_rebuild`；随后M4 OAuth/凭据管理、M5连接器/Skill、M6本地Docker及目标客户端验收。

开发入口：现有API健康/资源发现200，无凭据MCP401；继续 `pnpm dev`，管理端8001、员工端8002、API3000。作业工具通过MCP客户端调用，本批没有新增页面。

## 13. M3 派工、转队、确认与回收源码验收（2026-11-13）

恢复 `visit_task_assign`、`visit_task_transfer`、`visit_task_confirm`、`visit_task_return`，当前 **21/27 个完整工具**通过源码及隔离验收；门店工具仍只有查询分支。

- 派工/改派最多两名有效本队成员，空数组回收担当但保留进度；复用当前任务服务的角色能力、成员状态和版本检查。
- 转队限制当前队长本队、有效目标队、未完成任务；旧版混合选择会跳过已完成任务，全部已完成则拒绝。转队保留进展，清理原担当并经现有业务服务向接收队长及被移出者发送站内通知；通知使用当前服务的按任务发送方式，不复制旧聚合文案。
- 一批任务共享 MCP 写事务与成功审计；后续任务版本失败、目标不可用或审计失败时，先前转队、担当清理、日志和站内通知全部回滚。任务编号去重并排序锁定，可提供按任务编号的 tokens；未提供时在作业锁内读取快照。
- 单条确认认可原 reported_pct；批量确认仅选今日当前队长范围内、当前服务允许复核的待确认进展，不跨队。管理员仍遵守新系统既有审核条件，不能借MCP放宽修改待处理进展的限制；自动全确认会跳过当前无复核权限的行。
- 驳回将完成任务退至0–99，保留原reported_pct和审核轨迹。管理员退池只撤没有担当的任务，保留进度；仍有担当或已在池中返回旧字段 `skipped[{task_id,why}]`。未知任务编号按当前服务返回NOT_FOUND，不再静默忽略。
- 现有 `StationTasksService.change` 提取事务内调用入口，网页继续走原包装和CSRF；MCP不另写任务状态/进度/通知算法。任务名称的返回保持旧空名称，不把前端#id标签当成车站名。

验证：MCP权限单测及SDK集成两项、现有任务两项、前批填报两项、身份四项通过；覆盖只读/员工/跨队/离队拒绝、无效目标、版本冲突、批量与审计回滚、已完成跳过、确认/驳回、担当回收和退池跳过。验证通知接收者和进展保留，失败前后任务/担当/进度/日志/消息指纹一致。最终只补跑名称投影受影响的具名SDK及server类型检查/构建，没有重跑未受影响的财务/地图套件。

隔离运行器基准不变、临时库均清理；没有在真实任务上写入或发送通知。证据：`data/refactor/mcp-task-management-acceptance.json`。**源码与隔离验收完成；Docker未更新，生产未操作。**

剩余 **6 个完整工具**：`visit_store`写入分支、`visit_upload`、`visit_task_import`、`visit_payroll_export`、`visit_recon_export`、`visit_rebuild`；后续仍为M4 OAuth/凭据管理、M5连接器/Skill、M6本地Docker及目标客户端验收。

开发入口：继续 `pnpm dev`，管理端8001、员工端8002、API3000；当前健康与资源发现200，无凭据MCP401。本批工具经MCP客户端调用。

## 14. M3 门店主档写入源码验收（2026-11-13）

完成 `visit_store(action=merge/split/skip/apply)`，当前 **22/27 个完整工具**通过源码及隔离验收。实现为 `apps/server/src/mcp-store-write.ts`，调用 `StoreMasterService.previewInTransaction/processInTransaction`；现有网页/Worker仍使用原事务包装与队列入口。

- 保留候选合并/拆分/跳过/整批推荐的业务逻辑、财务锁、版本复核、跨城市组留人工、封账与历史发放保护，不另建合并算法。拆分确认语为 `确认拆分 <entity_id>`，整批为 `确认批量应用合并`。
- 可选 `dry_run=true` 以管理员read凭据预览并返回token；不写主档、工资或业务任务。正式操作需write scope，可带token防止预览后覆盖新状态；省略token时在当前事务中读取快照后由业务服务加锁复核。
- MCP保持同步执行返回，不新增第二套后台任务；原网页及Worker发布方式不变。用户note保存到现有日志，保留服务控制的请求标记；拒绝用户伪造该标记。
- 可选 `request_id` UUID用于安全重试；在审计中只存request_id与请求摘要，同一用户同一请求相同操作返回reused、不再次合并或重算。相同编号不同操作或其他入口未归属的日志标记返回REQUEST_ID_CONFLICT。无调用方编号时生成编号并返回，调用方应保存用于重试。
- 成功审计和业务写入同事务；模拟审计回填失败后，主档/候选/日志一起回滚。请求摘要不保存原始备注或凭据。批量结果含实际应用组数、实体数、留人工组和重算/封账跳过月份，不伪造缺失计数。

验证：MCP参数/权限单测及SDK集成通过，覆盖只读/非管理员拒绝、无写入预览、版本冲突、合并/拆分/跳过/批量、跨城市留人工、确认语、用户备注、请求复用/冲突及审计失败回滚。现有主档测试、管理查询64组对照及身份回归通过；既有主档回归包含下游重算失败回滚和封账/历史保护。最终请求归属补强后只跑受影响具名SDK及server类型检查/构建，均通过。

所有fixture在随机schema-only库内，未读取真实raw_records做验收；未启动额外Worker或排入真实任务。隔离运行器基准不变、临时库清理完成。证据：`data/refactor/mcp-store-write-acceptance.json`。**源码与隔离验收完成；Docker未更新，生产未操作。**

剩余 **5 个工具**：`visit_upload`、`visit_task_import`、`visit_payroll_export`、`visit_recon_export`、`visit_rebuild`；随后M4 OAuth/凭据管理、M5连接器/Skill、M6本地Docker及目标客户端验收。

开发入口：继续 `pnpm dev`，管理端8001、员工端8002、API3000；当前健康/资源发现200，无凭据MCP401。门店操作通过MCP客户端调用，无新增页面。

## 15. M3 Excel 车站任务计划导入源码验收（2026-11-13）

恢复 `visit_task_import`，当前 **23/27 个完整工具**通过源码和隔离验收。纠正M0的候选映射：`RailImportService`是铁路基础资产JSON导入，并非旧版线路执行计划Excel；本批补齐专用计划导入器 `apps/server/src/mcp-task-import.ts`，纯匹配规则位于 `packages/domain/src/task-plan-import.ts`，创建/派队复用当前 `StationTasksService` 事务服务。

- 表头在前12行识别，支持手动header_row和零起始cols；线路/运营方、括号别名、小假名、站群拆分、同名歧义及人工提示沿用旧规则。站群任一项对不上整行bad，不猜负责队，不新建车站，不覆盖既有任务。
- 必须识别出至少一个有效活跃负责队；已完成行、原任务保留行、无匹配团队行按规则跳过。新系统另外识别station_id关联的旧任务，防止只看place_id时漏掉已有任务。停用队不参与可导入目标匹配。
- 输入为content_base64或当前账号先前上传的blob_sha；path服务器路径模式关闭。16MiB以内的.xlsx私有保存在上传目录task_plan，按摘要校验及账号归属复用，不进入巡店imports；临时重排工作簿用后删除。API返回文件摘要，不返回服务器路径或原件内容。
- 默认preview只返回布局、汇总、拟建/bad行和plan_token，不创建作业；apply需blob_sha并重新匹配。提供plan_token时拒绝过期计划，bad行必须处理或明确skip_rows。旧版逐队commit改为整批创建、派发和成功审计同事务；中途/审计失败全部回滚。
- 创建前后核对所有既有任务归属不变；重复执行不重复建任务。MCP只增加带上限的JSON请求读取（24MiB），未提高浏览器业务接口全局上限；已测试大于100KiB的SDK上传请求。

验证：权限/文件/路径/列号规则单测及SDK集成通过。一份含7种计划行的合成Excel，其layout/summary/teams_in_file/rows与旧Python只读实现完整一致；额外验证跨账号blob拒绝、bad阻断、计划变化、显式skip、审计失败回滚、重复apply创建0、旧station任务保留和第0列手动匹配。现有任务及MCP身份回归通过；server/domain类型检查与构建通过。初次解析失败由ExcelJS对无窗口信息文件返回views为空引起，已修复；其后只补跑受影响集成及必要检查，不重复已通过单测或旧财务套件。

所有文件及数据均为本轮隔离合成fixture；未读取真实上传原件或raw_records做验收。临时文件/库已清理、基准不变。证据：`data/refactor/mcp-task-import-acceptance.json`。**源码与隔离验收完成；Docker未更新，生产未操作。**

剩余 **4 个工具**：通用巡店/对账上传 `visit_upload`、发薪导出 `visit_payroll_export`、对账导出 `visit_recon_export`、月重算 `visit_rebuild`；随后M4 OAuth/凭据管理、M5连接器/Skill、M6本地Docker及目标客户端验收。通用上传未据此标记完成。

开发入口：继续 `pnpm dev`，管理端8001、员工端8002、API3000；当前健康/资源发现200，无凭据MCP401。导入从MCP客户端传入合成或用户提供的文件，不新增前端页面。

## 16. M3 通用巡店/对账上传源码验收（2026-11-13）

恢复 `visit_upload`，当前 **24/27 个完整工具**通过源码与隔离验收。入口 `apps/server/src/mcp-upload.ts`，实际解析/判定/入表/工资刷新与对账处理沿用现有服务及Worker；没有另建结算流程。

- 根据模板sheet名、巡店/对账表头及手工结算sheet特征识别；kind支持daily_records/recon及旧visit别名。未知类型返回NEED_FILE_KIND、选项和有限表头信息，手工对照件返回专用指引，不自行猜类型。
- 对账月份可从日期列众数推断，不能推断时要求month；dry_run返回识别、预估行数/人数和可能替代的当前对账任务，不创建上传记录、后台任务或调用模型。六组合成文件结果与旧file_kind实现一致。
- 输入限16MiB以内的.xlsx base64，path模式保持关闭；暂存文件私有且用后移除，正式原件沿用现有private-files存储。返回任务编号和业务元数据，不返回本机路径或凭据。
- 将现有上传登记提供为事务内方法；MCP登记与成功审计共同提交后才调用现有enqueue，验证Worker可看到已提交记录。审计失败不登记业务、不入队，避免事务外发布任务。
- 保留巡店重复SHA拒绝、对账封账/版本替换和实际处理中的既有保护。对账未完成不替换当前成功版本；返回的replaced_previous_ids从完成后的实际替代关系查询，而非把预估当事实。
- 旧工具同步等待处理，新实现默认等待现有Worker最多30秒（可选wait_seconds=0–45），完成后返回实际结果；超时或wait_seconds=0明确返回accepted、pending/uploaded与job_id，不能当成解析/入表成功。未完成时pipeline_ok=false。调用方可用文件/任务查询继续跟进，不应盲目重传。
- 巡店入队失败保留失败文件，可走现有重试解析入口恢复；对账入队失败保留pending任务，由现有reconcile恢复。登记后暂不能查询后台状态时仍明确告知待确认，避免误导成未登记后重复上传。

验证：上传权限/参数单测及SDK集成通过，六组类型/预估对照一致；在隔离库和独立文件队列中调用真实巡店及对账processor，禁止付费模型调用，验证正式入表、对账完成、未完成不替代、重复/封账保护、审计失败不入队、两类队列失败及恢复、响应无私有路径。现有巡店上传、对账与身份回归通过，server类型检查/构建通过。仅补跑受队列状态与完成事实投影影响的集成，不重复未受影响的旧财务/地图套件。

首次失败发生在测试清理（统计表字段/表名误用），已改为现有schema，并确保清理异常时也关闭服务。只终止了本次挂起的隔离运行器，开发服务未中断；其随机库由运行器清理，留下的本轮合成文件目录也已识别并删除。后续测试正常完成并清理临时库/文件，基准指纹不变。证据：`data/refactor/mcp-upload-acceptance.json`。**源码与隔离验收完成；Docker未更新，生产未操作。**

剩余 **3 个工具**：`visit_payroll_export`、`visit_recon_export`、`visit_rebuild`；之后M4 OAuth/凭据管理、M5连接器/Skill、M6本地Docker及目标客户端验收。

开发入口：继续 `pnpm dev`，管理端8001、员工端8002、API3000；当前健康/资源发现200，无凭据MCP401。正式上传依赖现有Worker运行，无新增前端页面。

## 17. M3 月度重算源码验收（2026-11-13）

恢复 `visit_rebuild(preview/run)`，当前 **25/27 个完整工具**通过源码及隔离验收。实现位于 `apps/server/src/mcp-rebuild.ts`，复用 `SettlementService.rebuildTransaction`，没有复制判定、正式表、找平或统计同步逻辑。

- preview 在只读事务中统计正式行/点数、raw 判定分布、点数分布、预计入表数、从档命中和受影响人员；返回审计ID作为preview_id，同时保存数据/规则/账表指纹。
- run 要求当前管理员、write scope、同凭据30分钟内预演、确认语“确认重算 YYYY-MM”；执行前重新核对指纹和源记录。预演只允许使用一次，数据或配置变化、封账、缺源、未决申诉均拒绝。
- 正式重算复用现有财务锁和SettlementService，审计成功与业务写入同事务；审计失败时正式表、统计、期间表和重算快照一起回滚。历史实发台账不覆盖。

验证：预演不写数据、跨Token/过期/变化/封账/缺源拒绝、审计失败回滚、成功一次性消费预演及现有结算回归通过；server typecheck/build通过。所有fixture在schema-only隔离库，基准不变、临时库已清理。证据：`data/refactor/mcp-rebuild-acceptance.json`。**源码与隔离验收完成；Docker未更新，生产未操作。**

剩余 **2 个工具**：`visit_payroll_export`、`visit_recon_export`。两者暂未公布：发薪导出需要把文件生成、预览Token、登记付款与MCP成功审计整合到一致的事务边界；对账导出需要提供带管理员鉴权的私有下载入口。随后M4 OAuth/凭据管理、M5连接器/Skill、M6本地Docker及目标客户端验收。

开发入口：继续 `pnpm dev`，管理端8001、员工端8002、API3000；当前健康/资源发现200，无凭据MCP401。

## 18. M3 两类导出与工具恢复收口（2026-11-13）

完成 `visit_payroll_export`、`visit_recon_export`，至此 **27/27 个工具的源码与定向验收完成**。这不代表OAuth、连接器、用户客户端或Docker已验收；M4–M6继续在队列。官方SDK实测tools/list：员工6、队长10、管理员27，工具名无重复。

- 发薪导出适配 `apps/server/src/mcp-payroll-export.ts`，复用 `PayrollExportService.buildInTransaction` 和 `PayrollPaymentsService.previewInTransaction/registerInTransaction`。工资查询支持传入当前事务客户端，避免生成文件时另开事务读取不一致的账表；原网页包装、CSRF和登记语义不变。
- `dry_run=true`生成真正的XLSX预览，返回token、毛额与应付汇总，但不登记付款或核销；默认未提供确认语时也先返回预览，避免旧默认直接登记的误触。正式导出要求管理员write凭据、预览token和确认语 `确认导出发薪 YYYY-MM 第N期`。这个确认/版本参数是相对旧MCP的显式保护增强，连接器Skill须同步。
- 文件构建、复制到私有下载目录、付款登记、核销和成功审计在同一业务事务中；构建或审计失败不登记，失败私有文件清理。重复导出用最新预览，沿用已有付款不覆盖；旧预览因已发生登记而变化时要求重新预览，不把过期文件再次当成当前金额。
- 对账导出 `apps/server/src/mcp-recon-export.ts` 提取并复用原网页渲染逻辑 `ReconciliationExportService.write`：report包含月度/逐日差异、原始汇总、系统独有名单、已有归因及解读；detail只导出任务存储的原始对账汇总；diff导出月度和逐日差异。显式task_id可追溯历史，month选择未替代的当前任务，未完成拒绝。未从历史result_path直接复制文件，而从现有任务结果生成，输出以这些已存数据为准。
- 对账文件与已有解读过期标记在同一REPEATABLE READ事务内读取，不触发模型；网页原导出接口复用完整report模式，保持管理员Session鉴权。
- 共用私有文件入口 `/mcp/exports/<uuid>`：每次下载重新校验Bearer Token、活跃管理员及导出账号归属，审计成功提交前不可下载。UUID不是授权凭据；无凭据401、员工403、其他管理员404，撤销或停用后401。响应no-store，只有文件名/字节大小/SHA256/下载路径；4MiB内同时返回content_base64，超出仅返回鉴权下载地址，不暴露服务器路径。

验证：新增导出参数权限单测及SDK集成两项，读取实际XLSX单元格与工作表，覆盖预演不登记、确认语、旧token、构建失败、审计回滚/文件清理、重复登记、两期找平、对账三类导出、历史/未完成任务、存储解读、下载内容摘要/归属/撤销/停用及27/10/6发现数量。现有发薪文件、付款、工资读取/旧pay_plan、对账及身份回归通过；解读事务复用后补跑其现有网页导出回归，通过。server typecheck/build通过。最后只增补受影响的SDK矩阵检查，未重复前面未受影响的测试。

所有fixture均为schema-only随机库和临时私有目录；没有读取真实raw_records或签发真实账号Token，无付费模型调用。各次临时库由运行器删除，文件目录用后清理，基准数据指纹不变。证据 `data/refactor/mcp-exports-acceptance.json`。**源码/定向验收完成，Docker未更新，生产未操作。**

剩余阶段：M4 OAuth（发现、客户端注册、登录授权、PKCE、刷新、撤销）和Token管理界面；M5当前连接器与Skill（含新增确认/token、下载鉴权和未完成任务语义）；M6适用的全阶段检查、本地Docker网关/客户端闭环与回退验收。生产发布仍需单独授权。

开发入口：继续 `pnpm dev`，管理端8001、员工端8002、API3000；当前两端入口/健康/资源发现200，无凭据MCP401。下载使用返回的/mcp/exports路径及本账号有效管理员Bearer Token；OAuth尚未恢复。

## 19. M4 OAuth、授权页与凭据管理源码验收（2026-11-13）

恢复OAuth协议、独立浏览器授权页与管理员凭据面板；**M4源码及隔离验收完成，仍未发布Docker或生产**。实现为 `mcp-oauth.ts`、`mcp-oauth-http.ts`、`mcp-credentials.ts`，前端为 `OAuthAuthorize.tsx` 与 `McpCredentials.tsx`。复用原 `oauth_clients/oauth_codes/oauth_refresh_tokens/api_tokens` 结构，不迁移、不清空历史数据。

- 根路径发现 `/.well-known/oauth-authorization-server`、受保护资源发现、`/oauth/register`、`/oauth/authorize`、`/oauth/token`、`/oauth/revoke` 已接入。只支持公共客户端、授权码＋PKCE S256和刷新；不提供OpenID身份协议或客户端secret认证。协议请求16KiB上限、进程内限流，JSON/表单支持，错误不输出数据库细节；Token响应no-store。
- 回调精确匹配注册值，HTTPS网页/本机HTTP/原生自定义scheme可用，禁止脚本/文件等可执行协议、凭据、fragment或控制字符。resource要求与MCP公开地址一致。授权state按原值回传，由客户端验证，服务端不替客户端猜测。
- 授权码5分钟、一次使用；PKCE、client_id和回调必须匹配。交换与Token签发同事务，并发消费一成功一重放；重放吊销已交换的访问/刷新授权。新access沿用前8字符索引和SHA256，24小时有效；刷新90天，每次轮换旧值失效。三代刷新后撤销或重放旧值可使整条授权家族失效；家族摘要利用已有Token名称关联，不新增列或保存明文。
- 当前账号每次交换/刷新重新检查active/is_active、角色及首次改密；请假、停用、离职、删除或未改初始密码不能授权。refresh不能提升scope。旧表中有效refresh可直接轮换，旧access格式继续由原MCP校验；旧无家族信息授权被管理员撤销时保守撤销该账号旧刷新授权，不能承诺所有旧会话无条件保留。
- 与旧OAuth不同，新授权允许员工/队长在明确同意后取得write scope，使已恢复的本人报工/本队操作可用；仍由27项工具角色矩阵及实时成员关系决定能做什么，不授予管理员能力。旧read授权刷新不能借此升级，管理员scope也受请求范围约束。
- `/admin/mcp-authorize` 使用Appica现有主题，支持员工/队长/管理员登录和初始改密，保留授权参数；展示当前账号、客户端名称、精确回调和read/write说明，用户同意/拒绝后只跳转到服务端复核过的回调。支持切换账号、日文及错误状态，非法请求不展示允许按钮。
- 员工管理每行新增“MCP凭据”，管理员可以签发read/read,write和90天/永久凭据、查询前缀/状态、确认撤销。写请求沿用Session＋CSRF和事务内管理员检查；不能向停用/删除/未完成首次改密账号签发。明文只在签发响应显示一次，不放URL或持久化浏览器存储；关闭弹窗即清除，列表不返回摘要。OAuth家族凭据撤销同时阻断刷新。
- 开发授权页地址默认为 `http://127.0.0.1:8001/admin/mcp-authorize`，MCP公开入口由 `NEXT_MCP_PUBLIC_URL` 定义；新增可选 `NEXT_MCP_AUTHORIZATION_UI_URL`。生产要求HTTPS；本地Docker统一8000时须在M6显式配置对应公开入口，不能把开发8001默认当Docker验收结果。

验证：两项协议/回调测试通过，含PKCE错误、回调/resource不符、CSRF、拒绝、授权码重放/并发、三代刷新和家族撤销、权限不得升级、过期、强制改密/失效账号、旧刷新行兼容、手工凭据归属/签发/撤销和明文不可回读；相关MCP身份4项、既有登录/改密/CSRF/角色4项通过。server/admin类型检查与构建通过；隔离运行器相关检查通过。

新增 `node scripts/test-isolated.mjs --browser --oauth` 使用现有随机schema-only库及临时API/Worker。浏览器在390px验证登录→强制改密→授权→PKCE交换→员工6工具、拒绝、切换账号、日文及错误请求；桌面验证签发摘要、关闭不显示明文、取消及确认撤销。390/1440px无横向溢出，凭据弹窗在窄屏边界内，页面无异常。初次Chrome被沙箱拒绝后使用获准的沙箱外隔离验收；后续只修正测试的Appica combobox、关闭按钮和Toast角色定位，未改业务规则。没有额外截图或全量浏览器复跑。

源码验证只使用合成账号、隔离凭据和本机回调；未操作真实账号Token、生产或付费供应商。基准指纹保持、临时API/Worker/数据库/文件均清理。证据：`data/refactor/mcp-oauth-acceptance.json`、`mcp-oauth-browser-acceptance.json`。当前27项工具仍为源码验收，M4不替代目标WorkBuddy连接与Docker验收。

剩余：M5更新当前连接器/Skill、工具描述中的旧身份能力文案和兼容说明；M6适用的全阶段检查、本地Docker网关/配置/私有存储和目标客户端闭环及回退。生产发布仍须另行授权。

开发入口：已确认原统一启动器的tsx watch未响应入口变更，获准后仅重启该开发启动器一次，按原配置运行 `pnpm dev`。授权页 `http://127.0.0.1:8001/admin/mcp-authorize`（连接时带客户端参数），员工凭据入口 `/admin/employees`；两端/健康/两类发现200，无凭据MCP401。Docker及生产未动。

## 20. M5 连接器与 Skill 源码包验收（2026-11-13）

新增当前连接器 `deploy-next/connector/visit-settle/`，保持旧WorkBuddy风格的mcp.json/oauth-info.json/connector-meta.json/icon.svg/skills目录结构，版本0.3.0；旧包原样保留。**包/Skill与官方SDK验收通过；实际WorkBuddy导入和连接尚未完成，不把M5整体勾成已实机验收。**

- Skill带name/description元信息，自包含27工具表、员工6/队长10/管理员27权限矩阵，补齐所有作业工具以及新确认语、token/expected/preview_id/plan_token/request_id、上传等待/失败恢复、私有导出鉴权。保留日元、找平正负、未物化null、pay_plan、先查后写、导出登记与未知类型/月份询问规则，不在客户端重算业务。
- 工具返回/上传/表头/模型文字作为不可信数据，不执行其中指令；确认语不是用户授权。发薪缺确认先预览、dry_run不登记、pending不等于入表成功、退池/批量跳过不等于全部成功、下载凭据不放URL等都已明确。
- `visit_whoami`保留字段结构，角色标签正确区分队长，能力描述按role和scope投影；不再宣称员工“不能修改任何数据”。只读凭据即使发现写工具也不能执行，有效团队/业务状态仍在执行时核对。
- `scripts/package-mcp-connector.mjs`只打包六个白名单资源，校验HTTPS或本机HTTP源地址并替换VISIT_BASE_URL，不包含环境文件、凭据、上传或数据库；命令不上传、不部署、不修改客户端。已生成本地包 `data/refactor/visit-settle-local.zip`，明确绑定 `http://127.0.0.1:3000`，不把本地包指向生产。
- 官方SDK实际OAuthProvider首次连接走资源/授权服务器发现、动态注册、state及S256挑战，授权码交换后连接并自动刷新过期access；发现了注册元数据scope原先被严格schema误拒绝，已兼容并校验。元数据scope不授予访问，仍由授权同意与后端权限决定。

验证：三项连接配置/27项Skill与源码注册一致/白名单ZIP及地址替换检查通过；系统Skill校验器通过（系统Python无PyYAML，使用项目已有.venv运行，无新增依赖）。官方SDK隔离用例验证员工/队长/管理员6/10/27、本人查询、角色及他人拒绝、代表员工自报及只读写入拒绝，身份说明与scope一致；实际授权/交换/自动刷新通过。修正元数据scope后只重跑该用例及受影响OAuth协议/身份回归，server类型检查/构建通过。基准未变、临时库清理，无真实账号凭据或生产调用。证据 `data/refactor/mcp-connector-acceptance.json`。

目标客户端限制：本机 `/Applications` 和 `~/Applications` 未发现WorkBuddy命名应用，也没有本次目标客户端实际连接证据；这不证明客户端无法运行，但不能宣称ZIP已在WorkBuddy导入或执行Skill。未擅自安装客户端。目标客户端OAuth连接、工具/Skill发现、本人查询、权限拒绝和代表隔离写入仍是M5/M6的显式验收项。

剩余：M5的WorkBuddy实机连接与Skill加载；M6适用的全阶段检查、本地Docker网关/OAuth公开地址/私有导出存储、客户端闭环及回退。源码27工具/授权界面已可用，不表示Docker或生产已更新。生产不自动发布。

开发入口：继续 `pnpm dev`；API健康、OAuth发现、8001授权页200，无凭据MCP401。连接器本地包使用3000，不需要重新打包Docker。

## 21. M6 阶段检查与独立 Docker 候选验收（2026-11-13）

阶段检查、真实Docker候选及回退验收通过；**目标WorkBuddy实机与默认8000窄范围发布未完成，生产未操作**。当前源码27工具、OAuth与凭据界面、连接器均可供开发端验证，不能把临时Docker验收地址当作持续运行入口。

发布范围与产物：

- 相对上一实际本地发布 `local-staff-menu-20261112` 核对编译产物；MCP/OAuth影响API、管理端与Gateway，Worker使用同一API镜像。员工端没有本批变更，新增 `build --reuse-staff <release>` 按摘要复用旧HTML与7个资源，没有重新构建/上传员工静态产物。
- 上一manifest没有源码指纹，无法仅凭commit/dirty推断全部已部署字节；已为本次候选记录编译产物清单/摘要和API/Worker差异。当前源码相对旧发布还含已有团队、发薪入口等改动，本次不把它们自动纳入8000切流。
- 候选 `local-mcp-m6-20261113-a` 构建为本地arm64镜像，API/Worker与Gateway镜像摘要、HTML/资源及archive在 `.release/<id>/manifest.json`，有冻结编译产物inventory。镜像归档约233MB，未上传CDN/GCS、未操作生产。manifest标记acceptanceOnly，CLI增加拒绝误切流检查；本候选只用于隔离验收。
- 初次标准构建的245项测试和类型检查通过后，Docker Buildx被沙箱拒绝；同名重试又被不可变目录保护拒绝。随后仅从已经冻结的上下文续做Docker镜像及归档，没有删除旧release或重跑该步骤前已通过的检查。最后新增验收与防误切换检查后，最终全阶段检查为下述246项。

网关/公开地址：

- 替换候选Gateway对 `/mcp`、`/mcp/exports/*`、`/oauth/*`、两类发现路径的404排除规则，代理保留原路径和Host端口；MCP/OAuth在入口edge及内部gateway均支持300秒调用、25MiB请求和关闭代理缓冲，避免SPA吞掉协议或长调用超时。未知静态资源仍404。
- 本地正常部署配置明确 `NEXT_MCP_PUBLIC_URL=http://127.0.0.1:8000/mcp`、`NEXT_MCP_AUTHORIZATION_UI_URL=http://127.0.0.1:8000/admin/mcp-authorize`；开发端继续3000/8001，不修改开发数据库环境文件。本次临时Docker候选使用其独立随机入口origin，验证器自动配置同源地址。

真实Docker验收：

- `node scripts/test-isolated.mjs --docker-mcp` 复用现有schema-only随机库运行器，不在基准或开发完整副本写fixture。`scripts/verify-mcp-docker.mjs`为阶段验收创建独立新/旧网络、共享私有任务卷、候选及上一版API/Gateway/Worker和单独edge，8000原入口不动。
- 官方SDK经edge→Gateway→API完成自动发现/注册/PKCE授权码/连接及6/10/27工具发现；验证本人查询、角色拒绝和合成员工写入。Worker真实system.check完成；发薪预演不登记，私有下载摘要相符，API重启后文件与授权仍有效。
- 同一候选Docker入口中运行手机/桌面OAuth浏览器：登录、强制改密、同意/拒绝、切换账号、工具权限、凭据签发/关闭隐藏/取消撤销/正式撤销，中日文与390/1440布局通过，页面无异常。使用本机测试回调接收器，不将回调交给员工SPA处理。
- 独立edge回退到上一Gateway/API，候选Worker停下后旧Worker启动，健康与system.check通过；旧MCP/OAuth明确404。再停旧Worker、恢复候选Worker/edge，OAuth/私有文件和原凭据恢复；没有两个验收Worker同时消费同一队列。
- 最后清理本次创建的全部容器/网络/卷、临时API文件与随机数据库；额外docker列表核对无残留。8000原API/Worker/Gateway继续健康，local-state字节保持。证据 `data/refactor/mcp-docker-acceptance.json`、`mcp-m6-build.json`。

最终检查：**246项全量隔离测试，0失败/0跳过；7个工作区包typecheck和build通过**，14项Gateway/连接器补充检查通过。源码浏览器的其他未受影响地图组未重复跑；本阶段已运行实际候选Docker下的相关OAuth/员工凭据浏览器闭环。最终基准指纹不变、临时库已删除。汇总 `data/refactor/mcp-m6-acceptance.json`，工具/计划/链接及diff检查通过。

当前状态：源码开发8001/8002/API3000保留热更新；默认8000仍为 `local-staff-menu-20261112`，该旧版MCP/OAuth仍404。候选Docker实际跑过并通过验收，但验收环境现已清理，不宣称默认Docker已升级。

剩余：WorkBuddy实机入口尚未提供，无法完成导入ZIP、Skill加载及实际客户端连接；M5相关项继续待验收。如果要将MCP固定升级到8000，还需从已验证源码中准备排除其他既有改动的窄范围候选，再按适用差异验证/切流；验收快照禁止直接切流。生产发布仍需另行授权，不在本目标中自动执行。

## 22. M6 窄发布审核包（2026-11-13）

已生成 [窄发布审核包](../data/refactor/mcp-narrow-review-packet.md) 和机器可读清单 `data/refactor/mcp-narrow-review-packet.json`。它把22个MCP/OAuth专用文件与4个需要逐差异块审核的共享文件分开，并记录基准release、acceptanceOnly候选、员工资源复用、27项工具计数及外部验收证据。默认8000仍未切换，当前候选不能直接部署。

下一次正式窄候选必须从基准release重新构建，只纳入审核通过的共享差异；当前工作区的其他团队/界面/业务变化不因同一API镜像而自动纳入。WorkBuddy实机没有可用客户端入口，实机导入、Skill加载和代表流程仍需外部客户端证据。


## 23. WorkBuddy 实机准备（2026-11-13）

用户已安装 WorkBuddy，实际打开 `/Applications/WorkBuddy.app` 后界面显示 V5.9.3。旧记录中的“没有客户端入口”已解除；本次看到的入口为“专家·技能·连接器 → 连接器 → 自定义连接器 → 添加 MCP”，打开 `~/.workbuddy/mcp.json` 的 JSON 编辑器，尚未证明支持旧连接器 ZIP 直接导入。

编辑器原内容为 `{"mcpServers":{}}`，已填入 `visit-settle` 的 Streamable HTTP 配置，地址 `http://127.0.0.1:3000/mcp`、超时300000毫秒；界面显示“未保存”。保存持久连接配置的当次确认已向用户提出，尚未收到答复，不能把自动继续视为批准。原有插件 mcp-miora 在列表中显示8/8工具，本次没有修改。

本地端口3000、8001、8002已有监听进程，未重启服务、未重复执行已通过测试。未保存连接器、未完成OAuth、未安装Skill、未读取业务数据、未调用财务写入、未部署Docker或操作生产。

后续顺序与验收条件：

1. 用户确认后保存本地MCP配置；验收WorkBuddy能识别配置并进入连接或授权流程。
2. 核对OAuth实际授权页面与范围，在用户确认授权后连接；以客户端工具列表和调用结果验证身份及角色发现。涉及账号或业务数据发送至WorkBuddy时先明确数据范围。
3. 确认该版本Skill安装入口，准备可审阅的Skill，再按客户端安装确认要求执行；验收客户端可发现Skill并完成代表调用。
4. 将实机结果补入本计划；剩余Docker窄候选仍需以当前工作区（含未提交和新增文件）核对全部范围，不能直接使用第22节旧清单或acceptanceOnly快照切流。


## 24. WorkBuddy OAuth 与工具发现实机通过（2026-11-13）

用户已保存巡店 MCP 配置并点击首次信任。客户端随后显示“需要认证”；点击连接时，旧的开发API进程拒绝注册请求中的scope，未能打开授权页。源码已支持该字段，使用非法scope的无数据库写入探针可区分：旧进程返回invalid_client_metadata，加载当前源码后返回invalid_scope。没有放宽注册、回调地址、PKCE或角色校验。

本任务原开发进程未接收文件变更事件。以主机权限重新运行同一个 `pnpm dev` 后，实际观察到API与Worker的tsx Restarting，以及管理端/员工端的Vite hmr update；验证只触碰既有源码文件的修改时间，文件字节未改。尝试过的临时诊断和轮询配置全部撤回，开发脚本无额外差异。API readiness与两个前端CSS请求均200；沿用原本地配置，不构建Docker、不迁移或覆盖数据。

实机证据：WorkBuddy V5.9.3成功动态注册（201），授权页打开（303）；页面显示WorkBuddy Connector、本地3000 MCP、管理员和read/write范围。随后观察到用户完成登录/同意，服务端授权确认201、授权码交换200及MCP初始化200。WorkBuddy展开visit-settle后实际显示27/27个工具已启用，工具列表包含visit_whoami、visit_overview、visit_my_perf以及两类导出等全部27项。首次连接阶段证明管理员实机连接及工具发现；后续Skill与调用证据见下段。员工/队长的实机发现仍未验证。

Skill入口实测为“技能 → 添加技能 → 上传技能”，导入窗口支持包含SKILL.md的ZIP/文件夹和带YAML元信息的Markdown。已准备 `data/refactor/visit-settle-skill.zip`，仅含 `visit-settle/SKILL.md`，与源码字节一致，不含凭据或MCP配置。用户随后手动完成安装。已安装技能面板显示visit-settle，实际安装文件与源码SHA256一致。通过“去试试”选择Skill，执行一次仅限visit_whoami({})的验收请求。客户端运行日志确认Skill加载，以及mcp__visit-settle__visit_whoami的tool.call.completed为success（75ms），本次该服务只有这一次已完成工具调用；界面显示ok=true、role=admin、scopes=read/write、can_write=true。未查询工资、员工列表或业务明细，未执行业务写入；原始客户端日志未复制进项目验收文件。

本阶段管理员实机OAuth、27工具发现、Skill安装/加载与代表只读调用已通过。剩余：员工/队长实机角色验收；准备含未提交/新增文件在内的窄范围Docker候选。SDK既有6/10/27权限检查与隔离Docker验收不重复执行；默认8000与生产未部署。
