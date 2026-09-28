# 员工每日填报 + 区间对比分析报告 设计规格

> 状态：**v6（2026-09-27）**——在 v5 基础上补：**报告双视角**（管理员看全员 / 员工看自己）、**准确率口径**、**结构化报告 + 中/日双语**。
> 分支：`feat/staff-checkin`。本期不做上线。
> v6 新增：D13–D16；§6 报告结构改为结构化 JSON；§7 补准确率算法；§9 补员工端反馈页。

## 1. 要解决的问题

1. **员工每天报一次**：今天在哪个担当区域、跑了多少家 1 点店铺、多少家 2 点店铺。
2. **管理员上传文件后做对比**：例如上传 0916–0930 的文件、跑完判定入表后，把**文件算出来的真实结果**和**员工自报的数字**比，并拿到一份**大模型分析报告**：**谁报得准、谁偏差大、差在哪几天、可能什么原因、建议追问谁什么**。
3. **员工自己也要看得到反馈**：**哪天报得不准**、差了多少、自己的准确率怎样——让员工自己校正（这比管理员事后追着问有效得多）。

原则：

- **填报数据不参与工资计算**，也**不参与任何绩效判定**；它只用于"和文件结果对比"。
- **不改动任何现有表**；`person_daily_stats`（文件跑出来的结果）**只读**。
- **不拍照、不定位、不要 LBS、不涉及店名**——对比粒度是**数量**（人 × 日期 × 1点店数 / 2点店数），**不需要店铺对齐**。
- **数字由程序算，模型只负责写评语**——绝不让模型算差值（算错会误导管理决策）。

## 2. 已确认决策

| # | 决策 | 说明 |
|---|---|---|
| D1 | 填报频率 | **每天一次** |
| D2 | 填报内容 | **担当区域 + 1点店铺数 + 2点店铺数** |
| D3 | 不拍照 | 无照片、无 OSS |
| D4 | 不定位 | 无 GPS/LBS/地图，不申请 Google Maps key |
| D5 | 不选店名 | 无店名、无店铺对齐、无点位簿 |
| D6 | 不参与薪资 | 仅新增 2 张表；现有表一行不动 |
| D7 | 对比口径 | 人 × 日期：系统 `person_daily_stats.p1/p2` ↔ 自报 `p1_cnt/p2_cnt`；逐日 + 区间合计 |
| D8 | 数字由程序算 | 模型只写评语 |
| D9 | 报告触发 | 管理员手动点 |
| D10 | 不做纠错/补填（默认） | 只填当天、不可改（见 §12） |
| D11 | 不接 MCP、本期不上线 | 同前 |
| D12 | 报告可降级 | 异步、可重试、可一键关；AI 挂了对比数字照常看 |
| **D13** | **报告双视角** | **管理员看全员；员工只看到自己的那一段**（服务端裁剪，绝不返回他人数据） |
| **D14** | **报告结构化** | 模型输出 JSON（总体 + 逐人评语 + 逐人问题日期），不是一整篇散文——这样才能"同一个报告，两种视角各取所需" |
| **D15** | **报告双语** | 管理端中文、员工端**日文**各一份（现场是日本员工）；同一份数字，两段文字 |
| **D16** | **准确率不抵消** | 区间准确率用 **Σ\|偏差\|** 算，不用净差（净差会让"多报的和少报的互相抵消"，把不准的人算成很准） |
| **D17** | **员工端不显示排名** | 2026-09-27 确认：员工只看到**自己的**逐日偏差、准确率与评语；**不看名次、不看团队平均、不看他人数据** |
| **D18** | **报告生成后员工立即可见** | 2026-09-27 确认：不做"管理员发布后才可见"的二次审批；管理员点生成 → 员工端即可查看 |
| **D19** | **身份只认员工编号（客户已确认可提供）** | 人只按 `person_code` 认定：**不同编号 = 不同的人，不做合并、不做姓名猜配**。**本期新增「手工新建员工」，编号为必填**；导入时**按编号判定**：系统里已有 → 用系统里的（姓名/账号都用系统的），没有 → 创建。**这是现有导入逻辑的既有行为，导入侧零改动。** |
| **D20** | 手工建号的定位 | 给"**还没在任何文件里出现过的新员工**"先开户，让他当天就能开始填报；等文件来了，**按编号自动接上**，不需要任何关联/合并动作 |

## 3. 范围

**本期做**
1. 员工端：`/my/report` 每日填报 + 我的填报历史 + **`/my/report/feedback` 我的核对结果**（逐日偏差 + 我的评语）
2. 管理端：填报列表（含漏填标记）、**对比页**（逐人 + 逐日 + 准确率排名）、**一键生成分析报告**、导出 Excel
3. 对比与准确率：程序计算（只读 `person_daily_stats`）
4. 报告：结构化 JSON + 中/日双语文字、异步生成、状态可查、失败可重试、可整体关闭
5. **管理端「新建员工」**（`/staff-admin` 新增）：姓名 + **员工编号（必填）** + 登录名（可自动生成/手填）+ 初始口令 → 同时创建 `Person` 与 `User`；编号即身份键，导入时自动接上
6. 中日双语文案

**不做**：拍照/OSS、定位/LBS/地图、店名与店铺对齐、赛马榜、纠错补填（默认）、MCP、生产上线、**员工身份合并/姓名自动关联/孤儿数据清理**（不同编号即不同的人，见 D19/D20）。

## 4. 架构与文件

```
员工（手机/PC）                          管理员
  │ 每天填：区域 + 1点数 + 2点数           │ 上传文件 → 判定入表（现有流程）
  │ 事后看：我的逐日偏差 + 个人评语        │ 选区间 → 对比 → 生成报告（全员视角）
  ▼                                        ▼
app/routers/report_r.py   /my/report*   /staff-reports*
  ▼
app/services/daily_report.py   填报写入/列表 + 区间对比与准确率（纯读 person_daily_stats）
app/services/report_ai.py      组 prompt / 调模型 / 校验 JSON / 中英日双语落库
  ▼
2 张新表（每日填报 + 分析报告）
```

| 文件 | 职责 |
|---|---|
| `app/routers/report_r.py` | 员工端 + 管理端全部路由 |
| `app/services/daily_report.py` | 填报校验/写入/列表；**对比与准确率计算**（逐日 + 区间 + 排名） |
| `app/services/report_ai.py` | 组 prompt、调模型、**校验并解析 JSON**、双语生成、失败重试 |
| `app/templates/my_report.html` | 员工填报页 + 我的历史 |
| `app/templates/my_report_feedback.html` | **员工端：我的核对结果**（逐日偏差 + 个人评语） |
| `app/templates/staff_reports.html` | 管理端填报列表（含漏填标记） |
| `app/templates/staff_report_compare.html` | 管理端对比页 + 全员报告 |
| `app/models.py` | 追加 `StaffDailyReport` / `StaffReportAnalysis` |
| `app/config.py` | 追加设置项（§6.4） |
| `app/main.py` | 注册路由 + `STAFF_ALLOWED` 加 `/my/report` |
| `app/templates/base.html` | 员工端导航加「每日填报」；管理端加「填报与对比」 |
| `app/i18n.py` | 中文/日文词条 |
| `migrations/versions/*_staff_daily_report.py` | 建 2 表（`down_revision = "f7e8d9c0b1a2"`） |
| `AGENTS.md` + `docs/索引.md` | 按仓库约定同步 |

## 5. 数据模型（2 张新表）

### 5.1 `staff_daily_reports`（ORM `StaffDailyReport`）员工每日填报

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `person_code` | String(32) NOT NULL, FK `persons.code` | 填报人 |
| `user_id` | Integer FK `users.id` | 提交账号 |
| `report_date` | Date NOT NULL | **业务日 = JST 日期**（服务端算） |
| `area` | String(64) NOT NULL default "" | 担当区域（默认手输，见 §12） |
| `p1_cnt` | Integer NOT NULL default 0 | **1 点店铺数** |
| `p2_cnt` | Integer NOT NULL default 0 | **2 点店铺数** |
| `total_cnt` | Integer NOT NULL default 0 | 总店数 = p1 + p2（冗余） |
| `submitted_at` | DateTime NOT NULL | 服务端 UTC |
| `client_ts` | String(40) | 客户端时间（留痕） |
| `source` | String(16) NOT NULL default "web" | 预留 |
| `created_at` | DateTime NOT NULL | |

**唯一约束 `(person_code, report_date)`**：一天一条；重复提交 → 拒绝并提示「今天已填报」。
索引：`(report_date)`、`(person_code, report_date)`。校验：`p1_cnt`/`p2_cnt` 为 **0..999 整数**。

### 5.2 `staff_report_analyses`（ORM `StaffReportAnalysis`）对比分析报告

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `period_start` / `period_end` | Date NOT NULL | 对比区间 |
| `status` | String(12) NOT NULL default "pending" | `pending`/`running`/`done`/`failed` |
| `summary` | JSON NOT NULL default {} | **程序算出的对比结果**（逐人逐日、区间合计、准确率、排名、四类计数、`data_fingerprint`） |
| `payload` | JSON NOT NULL default {} | **模型产出的结构化评语**（`overall_comment` + `per_person[person_code]`，按语言分组，见 §8） |
| `ai_model` | String(64) NOT NULL default "" | 实际模型名 |
| `ai_tokens` | Integer NOT NULL default 0 | 本次 token 消耗（对账用） |
| `ai_error` | Text | 失败原因 |
| `created_by` | Integer FK `users.id` | 触发人 |
| `created_at` / `finished_at` | DateTime | |

- **`payload` 由员工端与管理端共用**：管理端渲染全部，员工端只渲染自己的 key——**同一个报告，两种视角**（D13/D14）。
- 同区间可留历史多份；同区间 + 同 `data_fingerprint` → **直接复用已有报告**，不重复烧 token。

## 6. 服务层接口

### 6.1 `app/services/daily_report.py`

```python
JST = timezone(timedelta(hours=9))      # 固定 +09:00（不用 zoneinfo）

def jst_today() -> date
def submit_report(db, user, *, area, p1_cnt, p2_cnt, client_ts="") -> StaffDailyReport
    # 校验：0..999 整数；已存在 (person_code, jst_today()) → raise AlreadySubmitted
def my_reports(db, person_code, *, month="", limit=60) -> list[dict]
def list_reports(db, *, start=None, end=None, person_code="", page=1, per=50) -> dict
    # 附"应填天数 / 实填天数 / 漏填日期"（应填 = 该区间系统有记录的日子）
def compare(db, start, end, person_code="") -> dict
    # 纯读 person_daily_stats + staff_daily_reports；结构见 §7；含逐日、区间、准确率、排名
def my_feedback(db, person_code) -> dict
    # 员工端：只返回**本人**的已生成报告区间 + 逐日偏差 + 本人的评语段落
def suggest_period(db) -> tuple[date, date]     # 默认区间 = formal_records 最近入表范围
```

### 6.2 `app/services/report_ai.py`

```python
def report_ai_enabled() -> bool                  # VISIT_REPORT_AI（默认 1）
def build_prompt(summary: dict, langs: list[str]) -> str   # 只喂汇总（含逐人 Top 偏差与问题日期），不喂全量原始数据
def start_analysis(db, user, start, end) -> StaffReportAnalysis   # 建 pending → 后台线程
def run_analysis(analysis_id: int) -> None       # running → 调模型 → 解析校验 JSON → done/failed
def retry(analysis_id: int) -> None
def person_block(analysis, person_code, lang) -> dict          # 员工端取本人段落（服务端裁剪）
```

- 复用 `app/services/ai_chat.py` 的 `chat()` + `extract_json()`（已带重试与 `reasoning_content` 兜底）。
- **模型返回必须是合法 JSON**；解析失败 → 重试一次 → 仍失败则 `failed`，但 **`summary` 数字仍然可用**，页面显示「AI 评语暂不可用，数字对比照常」。

### 6.3 报告语言（D15）

- **管理端报告 = 中文**；**员工端评语 = 日文**。生成时按"需要的语言"各出一份，存进 `payload.by_lang = {"zh": {...}, "ja": {...}}`。
- 语言集合由配置决定：`VISIT_REPORT_LANGS`（默认 `zh,ja`）。只配 `zh` 就是单语、省一半 token。
- 页面渲染时按当前界面语言取对应段落；缺失则回退到另一种语言。

### 6.4 配置项（`app/config.py`）

| 设置 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `report_ai_enabled` | `VISIT_REPORT_AI` | `1` | 0 = 关闭 AI 报告（对比数字不受影响） |
| `report_ai_langs` | `VISIT_REPORT_LANGS` | `zh,ja` | 报告语言集合 |
| `report_ai_max_tokens` | `VISIT_REPORT_AI_MAX_TOKENS` | `6000` | 输出上限（双语 + 逐人段落） |
| `report_ai_timeout` | `VISIT_REPORT_AI_TIMEOUT` | `300` | 单次超时（秒） |
| `report_ai_top_n` | `VISIT_REPORT_AI_TOP_N` | `15` | 喂给模型的偏差 Top N（控 token） |

模型复用现有 `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL`（走我方 AI 网关）。

## 7. 对比与准确率（程序计算）

数据源：**系统侧** `person_daily_stats`（`ref_date`、`person_code`、`p1`、`p2`、`records`、`points`，**只读**）；**自报侧** `staff_daily_reports`。

> 已实测口径一致：`p1 + p2 = records`、`points = p1 + 2×p2`，字段语义与员工要填的"1点/2点店铺数"完全对应。

**逐日比对**（人 × 日）

| 情况 | 归类 | 展示 |
|---|---|---|
| 两侧都有 | `both` | 系统 p1/p2 ↔ 自报 p1/p2；Δ1 / Δ2 / Δ总 |
| 系统有、自报无 | `missing_report` | **漏填报** |
| 自报有、系统无 | `missing_system` | 自报无系统记录（文件没覆盖/未做/口径不同） |
| 两侧都无 | 不展示 | — |

**偏差方向**：`Δ = 系统 − 自报 > 0` → **少报**；`Δ < 0` → **多报**。

**准确率（D16：不抵消）**

- 单日：`acc_day = max(0, 1 − |Δ总| ÷ max(系统总店数, 1))`
- 区间：`acc = max(0, 1 − Σ|Δ总| ÷ max(Σ系统总店数, 1))` —— 用**绝对值之和**，避免多报少报互相抵消
- **1点/2点分别再算一份**（结构偏差比总数偏差更能说明问题）
- 只统计 `both` 的日子；漏填报单独计（`实填天数 / 应填天数`），**不混进准确率**，避免"一天没填"和"报得不准"被混为一谈

**区间汇总与排名**（人 × 区间）：系统/自报的 1点、2点、总店数、Δ、偏差率、`acc`、实填/应填天数 → 按 `acc` 升序（最不准在前）+ 按 |Δ总| 降序两种排序都给。

**边界**：
- 区间内文件**尚未入表**（`person_daily_stats` 无数据）→ 提示「该区间还没入表，先上传并判定文件」，不生成空报告。
- **对比范围只含"两侧至少一侧有数据"的人**：既没有自报、也没有系统数据的身份**不进报告**（避免历史遗留账号变成噪音行）。
- 按 **D19**，同一个真人如果存在两个编号，报告里就是**两个人**（各自如实呈现），系统**不猜测、不合并**。

## 7.5 手工新建员工与「编号绑定」（D19/D20）

**为什么需要**：自报填报要求员工**先有账号**；而账号过去只能由"文件里出现该员工"触发，新员工在文件到达之前无法填报。客户已确认：**新建员工时可以填写员工编号**（即文件里的提交人编号）。

**建号表单**（管理端 `/staff-admin` 新增，admin 专属）

| 字段 | 必填 | 说明 |
|---|---|---|
| **员工编号** | **是** | 即文件里的 `submitter_code`（如 `2188240626279038`）。**必须与文件完全一致**，否则会被当成两个人 |
| 姓名 | 是 | 写入 `Person.display_name`；文件里同编号存在时**以系统里的为准**（导入只用不覆盖） |
| 登录名 | 否 | **输入姓名后由 AI 自动生成并回填**（可手改）；留空提交则服务端再生成一次。见下「登录名生成」 |
| 初始口令 | 否 | 留空 → 系统默认初始口令，且 `must_change_password=True`（首登强制改密） |

**写入**：`Person(code=编号, display_name=姓名, first_seen_import_id=None)` + `User(role="staff", person_code=编号, status="active", is_active=True, must_change_password=True)`。

**登录名生成（复用现有能力，不新造）**
- **交互**：姓名输入框**失焦（或点「AI 生成」按钮）** → 调 `POST /staff-admin/suggest-username`（admin 专属）→ 回填登录名；旁边给一个「重新生成」。输入姓名后 800ms 防抖，避免打多字时连发。
- **后端**：`ai_login_candidates([姓名])` 拿模型候选（中文→拼音 / 日文→罗马字 / 英文→原名清洗）→ `suggest_username()` 清洗 → `unique_username()` **自动去重**（同名的人加后缀）→ 返回 `{"username": "...", "source": "ai"|"rule"}`。
- **降级**：模型未配置 / 调用失败 / 返回不合法 → 自动回退**规则拼音或编号**（现有 `login_name_for` 就是这个行为）；页面按 `source` 提示「AI 未启用，已用规则生成」。
- **可手改**：生成结果只是**建议**，管理员可任意改；提交时若为空则服务端再生成一次（保证一定有值）。
- **缓存**：现有 `ai_login_candidates` 按姓名在**进程内缓存**——同名员工会拿到同一个候选，随后由去重逻辑加后缀（预期行为，不算 bug）。
- **消耗**：一次几十到一百 token，可忽略；但它是"建号即用 AI"的一个自然触点。

**校验**
1. 编号必填；**NFKC 归一 + 去首尾空白**（与文件写入口径一致，避免全角/空格导致"看起来一样但匹配不上"）；长度 ≤ 32（`Person.code` 是 String(32)）。
2. **编号已存在 → 拒绝新建**，提示「该编号已有员工：<姓名>」，并给出跳转（不许覆盖、不许建第二个）。
3. 姓名必填；登录名重复 → 自动加后缀（现有 `unique_username` 已实现）。

**导入时的行为（现有代码，不改）**
- `importer.parse_file`：`db.get(Person, code)` 命中 → 什么都不做；未命中 → 建 `Person`。
- `flow.ensure_persons`：命中 → **沿用系统里的 `display_name`**；未命中 → 建人；随后 `ensure_staff_user` **幂等**开账号（已有账号不重开）。
- 因此"手工建号 → 文件到达"是**自动接上**的，不需要任何合并/关联动作。这也是 D19 能成立的原因：**编号本身就是绑定键**。

**可选的一行改进**：导入命中的 person 若 `first_seen_import_id` 为空（手工建的），可补写为本次 `import.id`——这样"这个人首次出现在哪个文件里"可追溯。不做也不影响功能。

**给管理员的提示文案**：编号请**从文件里复制**，不要手打（少一位数字就会被当成另一个人，且系统不会自动纠正）。

## 8. 分析报告的结构（同一个报告，两种视角）

**流程**：管理员选区间 → 点「生成 AI 分析报告」→ 建 `pending` → 后台执行（`running`）→ 模型出 JSON → 校验 → `done`。页面可刷新看状态，失败可重试。

**模型输出（严格 JSON）**

```json
{
  "overall_comment": "整体一致性一句话 + 趋势",
  "accuracy_notes": "准确率分布与集中问题",
  "per_person": {
    "<person_code>": {
      "comment": "这个人的评语（客观、只谈数字与可能原因）",
      "off_days": [{"date": "2026-09-22", "delta": -7, "note": "少报 7 家"}],
      "questions": ["建议核实的问题"]
    }
  }
}
```

**两种视角的呈现（D13）**

| 视角 | 看什么 |
|---|---|
| **管理端**（中文） | 全员对比表 + 准确率排名 + `overall_comment`/`accuracy_notes` + 每人的 `comment`、`off_days`、`questions`（追问清单） + 导出 Excel |
| **员工端**（日文） | **只有自己**：逐日偏差表（程序算）+ 自己的 `comment` 与 `off_days`，**不含 `questions`、不含他人数据、不含排名数值**（除非开启，见 §12） |

**员工端文案边界（重要）**：评语**只陈述数字与可能原因**（"9/22 自报 12 家、系统核到 5 家，差 7 家"），**不做人身评价、不做定性指控**。管理端的"追问清单"**不展示给员工**——避免把管理工具变成员工的压力源，也避免 AI 措辞引发劳资摩擦。这条要写进 prompt 的约束里。

**token 量级**：一次生成 = 汇总输入（Top N 逐人 + 异常日）+ 双语逐人评语输出。双语会比单语多约一倍输出——这也是本功能主要的 token 消耗点。

## 9. 页面与权限

**员工端**

| 路由 | 说明 |
|---|---|
| `GET /my/report` | 填报页（日期=今天、担当区域、1点数、2点数）+ 今日已填内容 + 我的历史 |
| `POST /my/report` | 提交（**普通表单 POST + 303**，不需要 JS；已填报则拒绝） |
| `GET /my/report/feedback` | **我的核对结果**：按已生成报告的区间列出——逐日偏差表 + 我的评语；未生成 → 空态「本期数据还在核对中」 |

**管理端**

| 路由 | 说明 |
|---|---|
| `GET /staff-reports` | 填报列表：区间/人筛选；列 日期·员工·区域·1点·2点·总店数·提交时刻·漏填标记；分页 |
| `GET /staff-reports/compare` | 对比页：逐人区间合计 + 准确率排名 + 逐日明细 + 四类计数 + 报告区 |
| `POST /staff-reports/compare/analyze` | 生成报告（建 pending → 后台）→ 303 |
| `POST /staff-reports/analysis/{id}/retry` | 重试失败的报告 |
| `GET /staff-reports/export?kind=reports\|compare` | Excel 导出 |

另：**`POST /staff-admin/create`（新建员工：编号 + 姓名 + 登录名 + 口令）** 加在现有员工管理页上，与填报/对比页面同批实现。

- **权限**：`STAFF_ALLOWED` 加 `/my/report`（中间件 `path.startswith` 前缀匹配）；员工端校验 `role=="staff"` 且有 `person_code`；**员工端所有报告接口都必须按 `person_code` 裁剪**（服务端过滤，绝不把全员 payload 发给员工端）；管理端 `role=="admin"`；中间件 + 路由双层。
- 未改密员工会被重定向到 `/my/password`（`must_change_password`），留意验收账号。
- 页面文案走 `app/i18n.py`；模型产出的评语**不翻译**（本来就按语言各生成一份）。

## 10. 从 v4 删掉的东西（作废，不再实现）

门头照与照片表、阿里云 OSS 与存储抽象、Google Maps/Places、GPS 与精度留痕、店名与店铺实体对齐、点位簿、每日多次打卡、赛马榜、五类差异对比。

**连带结论**：`docs/谷歌地图API申请流程.md` 与 `docs/阿里云OSS开通清单.md` **本功能已不需要**（周一不必麻烦客户申请）。文档暂留，你要删我再删。

## 11. 与 AI 网关稳定性的关系（承接前面的讨论）

- 报告**异步生成**，不占用页面；`status` 可见；失败可重试。
- **降级链**：AI 不可用/关闭 → 对比数字与逐日偏差照常可看（员工端也照常能看到自己的数字表），只是没有评语。
- **缓存复用**：同区间 + 同 `data_fingerprint` → 复用已有报告，不重复消耗。
- **token 留痕**：每次生成记 `ai_model` 与 `ai_tokens`；管理端页面上显示本次消耗。
- 单次生成是"低频、可控"的调用（管理员手动触发），比"每张照片调用"对网关的瞬时压力小得多——**这是这套方案对稳定性更友好的地方**。

## 12. 口径默认值（如需调整说一声）

1. **不能补填、不能修改**：只填当天，重复提交直接拒绝。
2. **担当区域 = 手输自由文本**（≤64 字）；给固定清单就改成下拉 + 按区域汇总。
3. **报告手动触发**：管理员选好区间后点一下。
4. **默认区间**：取最近一次入表的日期范围，可手改。
5. **员工端不显示排名**（**已确认，2026-09-27**，见 D17）：员工只看到自己的逐日偏差、准确率与评语。
6. **报告生成后员工立即可见**（**已确认，2026-09-27**，见 D18）：无发布审批环节。

## 12.5 当前库的数据现状（仅备注，本期不处理）

排查发现（2026-09-27，已用历史备份库交叉验证）：

- **6 个无任何记录的历史身份**（`宗峰`/`高乔`/`潘春晓`/`高橋雅梨`/`YAN GUANGHE`，以及 `陈嘉溢` 的第二个编号 `2188240606650879`）。它们确实是**早期导入文件真实生成**的（`first_seen_import_id` 有值、person→账号的时序吻合），只是**那版文件已不在**：9/26 清库重传时 `imports` 被清空重建（id 从 1 重来），`persons`/`users` 保留了，于是编号悬空。
- **1 组重复账号**：`小川逸`（person `2188240606634082`）挂着 `ogawa` + `xiaochuanyi` 两个 active 账号（9/8 相隔 32 秒分别建立，命名风格一为模型罗马字、一为规则拼音）。
- 其中 `陈嘉溢` 属"**同一真人换了编号**"（现文件用 `2188240626279038`，977 条正式记录）。

**按 D19，本期一律不处理。** 若它们影响报告观感，用**现有功能**就能压住，无需新代码：
① 在「员工管理」把不需要的账号**停用**（孤儿/离职人员就不会再出现在填报与报告里）；
② 报告本身已按 §7 的"两侧至少一侧有数据"过滤，空对空的身份不会成行。

## 13. 明确不做

防作弊、补填审批、与工资/绩效的任何挂钩、照片、定位、店名、赛马榜、MCP 工具、生产上线、自动定时生成报告、把追问清单展示给员工。

**员工主数据相关（D19，本期一律不做）**：手工新建员工、手工建的员工与导入文件的身份关联、员工合并（含"同一人两个编号"）、孤儿账号/孤儿身份清理。

## 14. 集成注意

1. **时区**：业务日 = JST，固定 `timedelta(hours=9)`（不用 `zoneinfo`）。
2. **模板循环变量不可命名为 `t`**（覆盖 i18n 全局函数）：用 `r` / `row`。
3. **迁移 head**：`down_revision = "f7e8d9c0b1a2"`。
4. **SQLite/PG 双兼容**：Text 默认值 `sa.text("('')")`；唯一约束 `UniqueConstraint("person_code", "report_date")`。
5. **CSRF**：表单 POST 需带 `csrf_token`（复用 `csrf_ok`）。
6. **报告异步**：用后台线程 + 每线程自建 DB 会话（照抄 `ai_batch.run_batch_task` 的范式）。
7. **模型输出必须校验**：`extract_json` 解析 + 结构校验（缺 `per_person[code]` 时该员工页面回退为"只有数字、没有评语"），**不允许把未校验的模型输出直接展示**。
8. **员工端数据隔离**：`person_block()` 之外不得把 `payload` 整体传给员工端模板（防越权看到他人评语）。
