# 规格 · 团队管理（第一期）

> 状态：**待评审**（本文只描述设计，未实现代码；评审确认后再动代码）
> 日期：2026-10-02 起，**2026-10-03 按真实资料补充种子数据**（`万总/task_plan` + `万总/team_task`）
> **本期范围：只有「团队管理」**（用户 2026-10-02 决定：「这里边有两个功能，一个是团队管理，一个是任务管理」→ 拆成两个规格、两期交付，团队管理先行）
> 第二期：`docs/specs-station-tasks.md`（车站任务）
> 相关：`docs/specs-bd-ops-layer.md`（只借 `bd_` 前缀与"只新增表"硬边界）、`docs/specs-date-plan.md`（落点/权限基线）、`docs/索引.md`

---

## 0. 实施记录（2026-10-03 · **已实现**，规格与代码同源）

分支 `feat/team-task`。**本节是权威口径**，下面章节里与之冲突的地方以本节为准。

| 项 | 落地情况 |
|---|---|
| 数据表 | `bd_team` / `bd_team_member`（迁移 `b2c3d4e5f6a7`，down_revision `d3e4f5a6b7c8`） |
| 服务层 | `app/services/bd_teams.py`（create_team/update_team/list_teams/set_members/leader_teams/**is_leader_of**/teams_of_person/person_options/summary） |
| 权限基线 | `app/services/home.py::landing_home()`；`main.py` 的 `staff_isolation` 支持 `leader`；`auth_r` 的 `GET /`、`GET /login`、登录 POST 都走 `landing_home` |
| 落点 | **队长 → `/my/tasks`（员工端任务页）**，不再是 `/lead/team`（用户 2026-10-03：「队长端，也就是员工端里增加…」）→ **`/lead/*` 路由没有实现** |
| 导航 | `base.html`：管理端菜单改 `role == 'admin'`；底部 tabbar 对 `staff`+`leader` 都显示，新增「任务」tab |
| 账号 | `users.role` 新增 `leader`；`/staff-admin` 建号/编辑弹窗都加了**角色**选项（只允许 队员/队长，**不给 admin**），列表加「角色」列 |
| **角色自动同步** | `bd_teams.sync_account_roles()`：**在团队里当上队长 → 账号自动升 `leader`；不再当任何队队长 → 自动退回 `staff`**（只动 `staff`/`leader`，**绝不动 admin**）。`set_members` 保存成员后自动调用，种子脚本也会调。否则"指定了队长却进不去队长端"——本地库端到端实测踩到过：队长登录进的是队员视图、`/my/tasks` 显示 0 行 |
| 种子导入 | **一个脚本**：`scripts/bd_seed_team_task.py`（默认 dry-run，`--apply` 才写；`--teams-only`/`--stations-only`）。已把 6 队 + 5 位队长导入本地库；**陈嘉溢队 2 条同名人员记录 → 只报警不猜** |
| 测试 | `tests_web/test_team_task.py`（30 项，含队长无重定向环、导航 gate、队员只读等）；**全量 `tests_web` 357 passed** |
| 额外修复 | `tests_web/test_date_plan.py` 有 4 个用例没按仓库约定冻结 `jst_today`（JST 跨过 10-03 截止日后必红，**在干净基线上同样失败**）→ 已补 `frozen` fixture |

> ⚠️ 与下面章节的差异：§6.2「队长端 `/lead/team`」**未采用**（队长端并进员工端 `/my/tasks`）；
> §3.2 的 `landing_home` 队长分支 = `/my/tasks`。

---

## 1. 需求与本期范围

用户原话：「这里边有两个功能，一个是团队管理，一个是任务管理。」

| 期 | 模块 | 内容 |
|---|---|---|
| **第一期（本文）** | **团队管理** | 管理员建团队、指定队长、加减队员；新增队长角色；**全站权限与落点基线改造**；**导入 6 个真实团队 + 6 位队长** |
| 第二期 | 车站任务 | 管理员建车站（= 一片区域，用户口中的"车站"）、把一组车站派给团队、队长把车站分给 1~2 名队员、队长更新进展（担当/开始日/完成日/状态）、队员只读 |

**为什么团队管理必须先做**：任务管理里的"派给团队""分给队员"都以团队与队长角色为前提；
而且队长这个**第三个角色**会引爆现有一批写死的权限/落点（§3.3），这块地基必须先在
一个**功能极小、风险可控**的期里改干净，否则第二期同时改地基又加功能，出问题分不清是哪边。

---

## 2. 口径（已与用户确认）

| # | 分叉点 | 用户决定 |
|---|---|---|
| D5 | 队长身份 | `users.role` **新增 `leader`** |
| D7 | 交付方式 | **两个规格、两期交付：团队管理先行** |

### 2.1 本期明确不做

- ❌ 车站主数据、任务、派活、担当、进展（全部第二期）
- ❌ 队长改团队成员（用户口径是"管理员建团队、队长只管分派"）→ 队长端**只读**本队花名册
- ❌ 组织层级（集团/分公司/大区多层级）、绩效归属、团队业绩统计
- ❌ 与结算域（`formal_records` / `person_daily_stats` / `month_perf_records` / `payroll_*`）任何交互

### 2.2 种子数据：6 个真实团队 + 6 位队长（已逐条核对）

来源 `万总/team_task/`（6 份 `〈队名〉_负责车站及进度.xlsx`，2026-10-03）：

| 队名 | 站数 | 队长（`persons.display_name`） | `person_code` | 账号 | 账号状态 |
|---|---|---|---|---|---|
| 小川队 | 82 | 小川**逸** | `2188240606634082` | `ogawa` | active ✅ |
| 汤静队 | 76 | 汤静 | `2188240607339564` | `tangjing` | active ✅ |
| 甘子杰队 | 113 | 甘子杰 | `2188240606734603` | `ganzijie` | active ✅ |
| 罗子傑队 | 76 | 罗子傑 | `2188240606705205` | `luozijie` | active ✅ |
| 陈嘉溢队 | 86 | 陈嘉溢 | `2188240626279038` | `chenjiayi` | **resigned ⚠️ 登不进系统** |
| 陳偉鋒队 | 82 | 陳偉鋒 | `2188240606730380` | `chenweifeng` | active ✅ |

- **队名 = 队长名 + "队"**（`小川` → 队长 `小川逸`，名字不完全相等，靠模糊匹配）；
- **只有队长、没有队员名单**：`team_task` 的「担当」列**全空**，
  且自报数据里的"担当区域"只有 17 条、7 个取值，**与 515 站名单对不上**
  → **队员必须在 `/teams` 页面手工添加**（本期不做队员自动推导）；
- ⚠️ **陈嘉溢的账号是"离职"**（`can_login=False`），而他的队在 10-3 资料里是最大的在办队
  （计划表里 4 个小区域全是「开放」）。用户决定 **D13：先不动他的数据**，
  系统只做**只读提示**（`/teams` 列表 + 队长端空态提示"该队长账号不可登录"）；
- ⚠️ **陈嘉溢有两条 `persons` 记录**：`2188240626279038`（1981 条提交，绑定那个离职账号）
  与 `2188240606650879`（**0 条提交、无账号**）。按既有口径**不做身份合并**（不同编号 = 不同的人），
  导入时按"匹配到多条 → 报告出来"处理，**不猜**。其余 5 位队长均只有一条记录。


---

## 3. 角色与权限（⚠️ 本期风险最高的部分）

### 3.1 三个角色

| 角色 | `users.role` | 能做什么 | 登录落点 |
|---|---|---|---|
| 管理员 | `admin`（不变） | 建/改团队、指定队长、加减队员、管账号 | `/dashboard`（不变） |
| 队长 | **`leader`（新增）** | **本期只读**本队花名册；第二期负责分派与每日进展 | **`/lead/team`** |
| 队员 | `staff`（不变） | 不变（本期不给队员新增任何页面） | `date_plan.staff_home()`（不变） |

### 3.2 队长归属：两个来源，各管一件事

| 问题 | 依据 |
|---|---|
| 能不能进"队长端" | `users.role == "leader"`（账号角色，决定路由/中间件放行） |
| 管**哪个队** | `bd_team_member.role == "leader"` 且 `end_date IS NULL`（团队表，决定数据范围） |

- 一个账号可以是 **A 队队长 + B 队普通队员**（团队表多行）；
- 挂了 `leader` 角色但**没当任何队队长** → 队长端空态「你还没被指定为队长」；
- 队长**同时也是队员**时（有 `person_code`），他照旧能用 `/my/*` 员工端。

### 3.3 ⚠️ 加第三个角色会引爆的现状（`grep` 实测，不是推断）

| 形态 | 处数 | 现在跳向 |
|---|---|---|
| `def _denied(): return RedirectResponse("/login")`（`settle_r`/`stores_r`/`files_r`/`accounts_r`） | 主流 | `/login` |
| 写死的 `RedirectResponse("/my/perf")` | **4 处**（`accounts_r:41/85`、`report_r:202`、`plan_r:33`） | 员工绩效页 |
| `role != "admin"` 判断 | **50 处**（7 个 router） | 上两类 |

只有 `admin`/`staff` 两个角色时这套是自洽的，加 `leader` 后：

```
队长已登录，GET /
  【链条 A · 死循环】/
                  → 不是 staff → /dashboard
    /dashboard    → role != admin → _denied() → /login
    GET /login    → 有 session → _safe_next("") → "/"
    /             → 回到第一步 → ERR_TOO_MANY_REDIRECTS（浏览器报重定向过多）
  【链条 B · 落错页】/staff-plans → _admin_guard → /my/perf（他可能连 person_code 都没有）
```

**改法（唯一来源）**：新增 `app/services/home.py::landing_home(db, user)`：

```python
admin      → "/dashboard"
leader     → "/lead/team"          # 第二期做了任务后再评估是否改 /lead/tasks
staff      → date_plan.staff_home(db, user)   # 不复制它的逻辑，直接调
其他/未登录 → "/login"
```

落地清单（**四处都要改，漏一个就还有环**）：

1. `auth_r.root_redirect`（`GET /`）→ `landing_home()`；
2. `auth_r.login_page`（`GET /login` 已有 session 时）→ `landing_home()`，不再 `_safe_next("")`；
3. 各 router 的 `_denied()` → `landing_home()`（**未登录仍直接回 `/login`**，
   不能把匿名用户也送进 `landing_home` 去查库）；
4. 4 处写死的 `/my/perf` → `landing_home()`。

- `date_plan.staff_home()` **对外保持原样**（员工落点的既有唯一来源，多个测试盯着它），
  `landing_home` 是"多一个队长分支"的上层包装；
- **验收**：队长从 `GET /` 或任意管理端页面出发，**3 跳内落到 `/lead/team`，且路径不重复**。

### 3.4 中间件白名单

| 中间件/常量 | 改动 |
|---|---|
| `staff_isolation`（`app/main.py`） | 现在只处理 `role == "staff"`；**加 `leader` 分支**：① `must_change_password` → 拦到 `/my/password?must=1`（与员工同规则，**不能漏**）② 非白名单路径 → 回 `/lead/team` |
| `STAFF_ALLOWED` | **本期不动**（队员端没有新页面；第二期才加 `/my/tasks`） |
| `LEADER_ALLOWED`（新增） | `/lead/`、`/my/`（他可能同时是队员，要看自己的绩效/自报/出勤计划）、`/login`、`/logout`、`/static`、`/healthz`、`/product`、`/oauth/`、`/.well-known/` |

> 队长**不进**管理端白名单：`/teams`、`/staff-admin`、`/dashboard` 等一律只有 `admin` 能进
> （`role != "admin"` 这层过滤对队长天然有效，✅ 不用逐个改）。

### 3.5 模板层两处 `role` 判断（`base.html` 实测）

| 位置 | 现状 | 加 `leader` 后的后果 | 改法 |
|---|---|---|---|
| `base.html:18` | 顶栏管理菜单包在 `{% if current_user.role != 'staff' %}` 里 | **队长看到整排管理菜单**（看板/文件/绩效/对账/找平/配置…），**点哪个弹哪个** | 外层改 `role == 'admin'`；leader 单独一组导航（见 §6.4） |
| `base.html:11` | `<body class="has-tabbar">` 只在 `role == 'staff'` 时加 | 队长**没有底部 tabbar**，窄屏（≤860px）只剩汉堡菜单 | leader 也给 `has-tabbar`（并实测窄屏可用） |

### 3.6 账号侧：`/staff-admin` 支持 `leader`

- 新建员工 / 编辑弹窗里**加"角色"选项**：`staff` / `leader`（`admin` 要不要给选项？
  → **不给**，避免误操作把普通员工提成管理员；管理员账号继续用现有方式管理）；
- 改角色**只影响权限**，不动 `persons`、不动任何业务数据；
- 队长账号**建议绑定 `person_code`**（他通常也在一线），但**不强制**。

---

## 4. 数据模型（2 张新表）

> **硬边界（沿用 bd-ops）**：只新增 `bd_*` 表，**绝不向结算域四表加列**
> （`formal_records` / `person_daily_stats` / `month_perf_records` / `payroll_period_rows`）；
> **结算域永不读本域**。守门测试 `test_team_mgmt_does_not_touch_settlement_tables`。
> 约定同仓库：`Integer` 自增主键、`DateTime default=_now`、`Text` 用 `default=""`、状态用 `String(16)` 不用 Enum。

### 4.1 `bd_team` —— 团队

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `name` | String(64) | 队名（必填） |
| `code` | String(32) | 队编号（可空，**UNIQUE**，手工填；判重只提示不覆盖） |
| `note` | Text | 备注 |
| `status` | String(16) | `active` / `closed`（停用后不再出现在派活候选；成员与历史保留） |
| `created_by` | String(64) | 创建人（管理员登录名） |
| `created_at` / `updated_at` | DateTime | |

> 只做**一层队**。"多层级组织"（bd-ops 设计里的 `parent_id`/`level`）**本期不加**——
> 用户口径「规则是死的，人是活的，宽松一点」，等真有需求再加列。

### 4.2 `bd_team_member` —— 团队成员（含历史）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `team_id` | Integer | → `bd_team.id` |
| `person_code` | String(32) | 关联既有 `persons.code`，**不新建人员主数据** |
| `role` | String(16) | `leader` / `member` |
| `start_date` | Date | 加入日 |
| `end_date` | Date | 离开日；**NULL = 现役** |
| `created_at` | DateTime | |

- **UNIQUE(`team_id`, `person_code`, `start_date`)**；
- 一个人可以在多个队（多行）；
- 移出成员 = **写 `end_date`，永不删行**（历史可追溯，与仓库既有"数据不删"口径一致）；
- 一个队**允许多个 leader**（宽松），但管理员端提示"这个队还没有队长"。

### 4.3 种子导入（`scripts/bd_seed_teams.py`，默认 **dry-run**，`--apply` 才写）

```
--src /Users/liuchang/Desktop/万总/team_task    # 6 份 Excel（仓库外，必须显式传）
```

| 步骤 | 规则 |
|---|---|
| 1 | 6 个文件名 `〈队名〉_负责车站及进度.xlsx` → `bd_team(name=队名, status='active')` |
| 2 | 队名去掉末尾"队" → 在 `persons.display_name` 里匹配队长 → `bd_team_member(role='leader', start_date=今天)` |
| 3 | 匹配结果**逐条报告**：唯一命中 → 挂上；命中 0 条 → 报警（不建队长）；**命中多条 → 报警并跳过**（陈嘉溢就是这种） |
| 4 | **不导入队员**（资料里没有队员名单，见 §2.2）→ 报告里写明"每队现有成员 = 1 人（队长）" |
| 5 | 幂等：重复跑不重复建队（按 `bd_team.name` 判重）、不重复挂队长（按 `(team_id, person_code)` 现役判重） |

> ⚠️ **不要把这个脚本挂进进表/上传流程**：它是**一次性建队工具**，跑完就能删（或留作重建演示库用）。
> 第二期的 `scripts/bd_seed_stations.py` 复用同一批文件，只是多导入 515 个车站与所属队。

---

## 5. 服务层 `app/services/bd_teams.py`（口径唯一来源）

> 与仓库既有风格一致：口径全在服务层，路由只做校验与跳转；**渲染路径不写库**。

| 函数 | 作用 |
|---|---|
| `norm_code(s)` | 队编号归一（NFKC + 去空白） |
| `create_team(db, name, code, note, by)` | 建队；`code` 重复 → `CodeExists`（只提示不覆盖） |
| `update_team(db, team_id, name, code, note, status, by)` | 编辑/停用 |
| `list_teams(db, kw, status)` | 列表 + 关键词搜索 + **每队人数/队长**（一次聚合，别 N+1） |
| `set_members(db, team_id, rows, by)` | 覆盖式保存成员（新增/移出/改角色）；被移出的写 `end_date` |
| `leader_teams(db, person_code)` | 该人当队长的队（`role=leader` 且未结束） |
| `team_members(db, team_id, active_only=True)` | 现役成员（带 `persons.display_name`、**有无账号**标记） |
| `is_leader_of(db, person_code, team_id)` | 第二期要用（越权校验的唯一入口，本期先建好） |
| `team_options(db)` | 下拉（`active` 队） |

---

## 6. 路由与页面

### 6.1 管理员（`role == "admin"`）

| 路由 | 页面/动作 | 说明 |
|---|---|---|
| `GET /teams` | 团队列表 | 每行：队名 / 编号 / 队长 / 人数 / 状态 / 操作 |
| `POST /teams/create` | 新建 | 表单 + `{{ form_token() }}` |
| `POST /teams/{id}/edit` | 编辑/停用 | 同上 |
| `POST /teams/{id}/members` | 成员保存 | 多选人员（**`data-emp-filter` 可搜索 combobox**）+ 每人角色（队长/队员） |

- 成员下拉数据源 = 有账号的 `persons`（`/staff-admin` 里建的人），
  显示「姓名 + 编号后 5 位」（`date_plan.short_code` 同口径）；
- 界面上明确提示：**没有账号的人看不到任何页面**（第二期他也就看不到"我的区域"）；
- 新增/编辑用**原生 `<dialog>` 弹窗**（同 `/staff-admin` 的既有做法，不引脚本库）。

### 6.2 队长（`role == "leader"`）

| 路由 | 说明 |
|---|---|
| `GET /lead/team` | 我的队（可能多个）：队名 / 编号 / 队员名单（姓名 + 编号后 5 位 + 角色 / 加入日）|

- **只读**（本期）：不加/不移成员，不新建队；
- 空态：`你还没被指定为队长`；
- 队长**可以看自己的绩效/自报/出勤计划**（`/my/*`），顶栏给入口。

### 6.3 队员（`role == "staff"`）

**本期不新增任何页面**，员工端行为完全不变（这是本期"功能极小"的落点）。

### 6.4 导航（`base.html`）

- **管理员顶栏**加 1 项：`团队`（/teams）；
- **队长**：顶栏只留 `我的团队`（/lead/team）+ `我的绩效`（/my/perf）+ `每日填报`（/my/report）
  +（若需）`出勤计划`，**不给任何管理端链接**；
- 窄屏（≤860px）：队长也要有可用导航（§3.5 的 `has-tabbar`）。

---

## 7. 关键规则（写死在测试里）

1. `role != "admin"` 覆盖不到的地方**只有** §3.3 那四处 + §3.5 两处模板判断，**逐条改净**；
2. 队长落点唯一来源 = `landing_home()`；**队长不会掉进重定向环**；
3. 队长的数据范围 = `bd_team_member` 里他当 `leader` 的队；**改 URL 里的 team_id 也看不到别队**
   （第二期的任务越权校验也走同一个 `is_leader_of`）；
4. 移出成员 = 写 `end_date`，**不删行**；再拉回来 = 新增一行（新的 `start_date`）；
5. 队编号重复 → **只提示不覆盖**（同「编号即身份键」既有口径）；
6. 队长 `must_change_password` 时**先改密**（与员工同规则，别漏）；
7. 停用团队（`status=closed`）→ 不再出现在派活下拉（第二期），**成员与历史照旧可查**；
8. 队长**不能**改团队与成员（本期只读）；管理端写操作**只有 `admin`**；
9. 所有 POST 表单带 `{{ form_token() }}`；所有导出链接带 `download`；
10. 界面上队长人数为 0 的队要有明确提示（否则第二期派活了没人能分派）。

---

## 8. 边界与坑

| # | 坑 | 处理 |
|---|---|---|
| 1 | **不碰结算域** | 只新增 2 张 `bd_*` 表；守门测试 |
| 2 | **重定向死循环**（§3.3 链条 A） | 四处一起改 `landing_home()`；测试断言 3 跳内落到 `/lead/team` 且路径不重复 |
| 3 | `users.role` 无 CHECK 约束 | 新值 `leader` 是纯字符串；**逐条核对 `role == "staff"` 的判断点**（`main.py`、`auth_r`、`base.html`）→ 列清单后逐个过 |
| 4 | **既有测试盯着员工落点** | `staff_home` 对外行为不变；改完**必须跑全量** `tests_web` |
| 5 | 迁移 | 新迁移 `down_revision = "d3e4f5a6b7c8"`（当前 head）；`tests_web/test_migrations.py` 加新表；**本地库不跑 alembic → 手工建表** |
| 6 | i18n | 新页面文案一律 `t('中文')` + `app/i18n.py` 补 `ja`；**模板循环变量不要用 `t`**；`scripts/i18n_wrap.py` 之后人工查破损 |
| 7 | 防重复提交 | 每个 POST 表单 `{{ form_token() }}` |
| 8 | 员工下拉 | 成员多选**必须加 `data-emp-filter`**（否则没有搜索框 —— 用户已经为此发过一次火） |
| 9 | 反范式 | 列表页人数/队长用**一次聚合查询**，不要每行查一次 |
| 10 | 队长与员工身份重叠 | 队长可能是队员：`/my/*` 必须照常可用（LEADER_ALLOWED 覆盖），别把人挡死 |

---

## 9. 测试 `tests_web/test_team_mgmt.py`

| # | 用例 |
|---|---|
| 1 | 建队/编辑/停用；队编号重复只提示不覆盖；队编号归一（全角/空格） |
| 2 | 成员增删改角色；移出写 `end_date` 不删行；再拉回来是新行 |
| 3 | 现役成员过滤（`end_date IS NULL`）；一个人多队 |
| 4 | `landing_home()` 三分支 + **队长不构成重定向环**（`/` → … → `/lead/team`，路径不重复） |
| 5 | 队长 `must_change_password` → 拦到 `/my/password?must=1` |
| 6 | 队长越权：`/teams`、`/staff-admin`、`/dashboard` → 回 `/lead/team`；改别队 team_id → 看不到 |
| 7 | 队长端只读：对 `/teams/*` 写端点 POST → 拒绝 |
| 8 | 导航 gate：队长页面 HTML **不含** `/dashboard` `/files` `/perf` `/config`；`has-tabbar` 在场 |
| 9 | 员工端零回归：`staff` 的所有落点与页面不变（跑全量 `tests_web`） |
| 10 | 守门：`test_team_mgmt_does_not_touch_settlement_tables` |
| 11 | i18n：新页面 zh/ja 都有文案；成员下拉有 `data-emp-filter` |
| 12 | 没有队长的队有提示；空态文案 |
| 13 | **种子导入**：6 队 + 6 队长；队长名模糊匹配（`小川`→`小川逸`）；**命中多条 → 报警跳过**（陈嘉溢）；幂等重跑不重复；**不导入队员** |
| 14 | **队长账号不可登录**（陈嘉溢队现状）→ `/teams` 与队长端都有提示，且**不阻断其他队** |

---

## 10. 待确认（评审时请一并回答）

| # | 问题 | 我的默认 |
|---|---|---|
| Q1 | 队长要不要能**自己加/移队员**？ | 默认**不能**（你说的是"管理员建团队、队长只管分派"）；要放开我改 |
| Q2 | 一个队**要不要限制只有一个队长**？ | 默认**允许多个**（宽松），管理端提示缺失 |
| Q3 | 队长（有 `person_code`）要不要**弹出勤计划填报提示**？ | 默认**不弹**（`/my/plan` 自己进得去）；要弹我就接 `needs_plan` |
| Q4 | `/staff-admin` 里要不要放开"角色 = admin"？ | 默认**不放开**（避免误把普通员工提成管理员） |
| Q5 | 队名用**文件名原文**（`小川队`/`陳偉鋒队`）吗？ | 默认**原文照抄**（含繁体，与 `persons.display_name` 写法一致） |
| Q6 | 6 个队用**脚本导入**还是管理员手工建？ | 默认**脚本导入**（`scripts/bd_seed_teams.py`，默认 dry-run）；手工建仍可用 |
