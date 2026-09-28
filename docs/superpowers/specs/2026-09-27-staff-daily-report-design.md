# 员工每日填报 + 区间对比分析报告 设计规格

> 状态：**v5（2026-09-27）**——按客户最新沟通**整体重做**：员工端从"随时随地打卡"改为**每天上传一次日报**，**不拍照、不定位、不要 LBS**；管理端在文件上传后做**对比 + 大模型分析报告**。
> 分支：`feat/staff-checkin`。本期不做上线。
> v4 及以前的内容（门头照 / 阿里云 OSS / Google Maps / 店名对齐 / 点位簿 / 赛马榜）**全部作废**，见 §10。

## 1. 要解决的问题

1. **员工每天报一次**：今天在哪个担当区域、跑了多少家 1 点店铺、多少家 2 点店铺。
2. **管理员上传文件后要能对比**：例如上传了 0916–0930 的巡店文件、跑完判定入表后，把**文件算出来的结果**和**员工自报的数字**摆在一起比，并让**大模型给一份分析报告**（谁报得准、谁偏差大、差在哪几天、可能什么原因、建议追问谁什么问题）。

原则：

- **填报数据不参与工资计算**，也**不参与任何绩效判定**；它只用于"和文件结果对比"。
- **不改动任何现有表**；`person_daily_stats`（文件跑出来的结果）**只读**。
- **不拍照、不定位、不要 LBS、不涉及店名**——对比的粒度是**数量**（人 × 日期 × 1点店数 / 2点店数），**不需要任何店铺对齐**。

## 2. 已确认决策

| # | 决策 | 说明 |
|---|---|---|
| D1 | 填报频率 | **每天一次**（不是随时随地打卡） |
| D2 | 填报内容 | **担当区域 + 1点店铺数 + 2点店铺数**（总店数自动算出） |
| D3 | 不拍照 | 无照片、无 OSS、无鉴权取图 |
| D4 | 不定位 | 无 GPS、无 LBS、无地图、不申请 Google Maps key |
| D5 | 不选店名 | 无店名输入、不与店铺主档对齐、无点位簿 |
| D6 | 不参与薪资 | 仅新增 2 张表；现有表一行不动 |
| D7 | 对比口径 | **人 × 日期**：系统侧 `person_daily_stats` 的 `p1`/`p2` ↔ 自报的 `p1_cnt`/`p2_cnt`；逐日 + 区间合计两个层次 |
| D8 | 报告由大模型生成 | 报告正文由模型出（走我方 AI 网关）；**对比数字一律由程序算**，不给模型算 |
| D9 | 报告触发 | **管理员手动点**（不自动跑，避免白花钱、也便于选区间） |
| D10 | 不做纠错/补填（默认） | 只填当天、提交后不能改（见 §11 默认值） |
| D11 | 不接 MCP、本期不上线 | 同前 |
| D12 | 报告必须"不阻塞、可降级" | AI 挂了照样能看对比数字；报告异步生成、失败可重试、可一键关 |

## 3. 范围

**本期做**
1. 员工端：`/my/report` 每日填报（担当区域 + 1点数 + 2点数）+ 我的填报历史
2. 管理端：填报列表（按区间/按人）、**对比页**（逐人 + 逐日）、**一键生成大模型分析报告**、Excel 导出
3. 对比计算：程序完成（只读 `person_daily_stats`）
4. 报告：异步生成、状态可查、失败可重试、可整体关闭
5. 中日双语文案

**不做**：拍照/OSS、定位/LBS/地图、店名与店铺对齐、赛马榜、纠错补填（默认）、MCP、生产上线。

## 4. 架构与文件

```
员工（手机/PC）                         管理员
  │ 每天填一次：区域 + 1点数 + 2点数      │ 上传文件 → 入表（现有流程）
  ▼                                       ▼
app/routers/report_r.py  /my/report*   /staff-reports*
  ▼
app/services/daily_report.py     填报采集 / 列表 / 区间对比计算（纯读 person_daily_stats）
app/services/report_ai.py        大模型分析报告（异步、可重试、可关）
  ▼
2 张新表（员工填报 + 分析报告）
```

| 文件 | 职责 |
|---|---|
| `app/routers/report_r.py` | 员工端 + 管理端全部路由 |
| `app/services/daily_report.py` | 填报校验/写入/列表；**区间对比计算**（逐日 + 区间合计 + 四类分类） |
| `app/services/report_ai.py` | 组 prompt、调模型、落库报告、失败重试 |
| `app/templates/my_report.html` | 员工填报页 + 我的历史 |
| `app/templates/staff_reports.html` | 管理端填报列表（含漏填标记） |
| `app/templates/staff_report_compare.html` | 对比页 + 报告展示 |
| `app/models.py` | 追加 `StaffDailyReport` / `StaffReportAnalysis` |
| `app/config.py` | 追加设置项（§6.3） |
| `app/main.py` | 注册路由 + `STAFF_ALLOWED` 加 `/my/report` |
| `app/templates/base.html` | 员工端导航加「每日填报」、管理端加「填报与对比」 |
| `app/i18n.py` | 中文/日文词条 |
| `migrations/versions/*_staff_daily_report.py` | 建 2 表（`down_revision = "f7e8d9c0b1a2"`） |
| `AGENTS.md` + `docs/索引.md` | 按仓库约定同步 |

## 5. 数据模型（2 张新表）

### 5.1 `staff_daily_reports`（ORM `StaffDailyReport`）员工每日填报

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `person_code` | String(32) NOT NULL, FK `persons.code` | 填报人（与正式表同口径） |
| `user_id` | Integer FK `users.id` | 提交账号 |
| `report_date` | Date NOT NULL | **业务日 = JST 日期**（服务端算） |
| `area` | String(64) NOT NULL default "" | **担当区域**（默认手输；若要固定清单见 §11） |
| `p1_cnt` | Integer NOT NULL default 0 | **1 点店铺数** |
| `p2_cnt` | Integer NOT NULL default 0 | **2 点店铺数** |
| `total_cnt` | Integer NOT NULL default 0 | 总店数 = p1 + p2（冗余，方便列表/导出） |
| `submitted_at` | DateTime NOT NULL | 服务端 UTC 提交时刻 |
| `client_ts` | String(40) | 客户端时间（留痕） |
| `source` | String(16) NOT NULL default "web" | 预留 |
| `created_at` | DateTime NOT NULL | |

**唯一约束 `(person_code, report_date)`**：一天一条。重复提交 → 拒绝并提示「今天已填报」（D10）。
索引：`(report_date)`、`(person_code, report_date)`。
校验：`p1_cnt` / `p2_cnt` 必须为 **0..999 的整数**（别的值拒绝）；`area` 可空但建议必填（见 §11）。

### 5.2 `staff_report_analyses`（ORM `StaffReportAnalysis`）对比分析报告

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `period_start` / `period_end` | Date NOT NULL | 对比区间 |
| `status` | String(12) NOT NULL default "pending" | `pending` / `running` / `done` / `failed` |
| `summary` | JSON NOT NULL default {} | **程序算出的对比结果快照**（区间合计、偏差排名、四类计数、数据指纹） |
| `ai_text` | Text NOT NULL default "" | 大模型报告正文（Markdown） |
| `ai_model` | String(64) NOT NULL default "" | 实际使用的模型名（留痕） |
| `ai_tokens` | Integer NOT NULL default 0 | 本次消耗 token（**对账用**） |
| `ai_error` | Text | 失败原因 |
| `created_by` | Integer FK `users.id` | 触发人 |
| `created_at` / `finished_at` | DateTime | |

- 同一区间可保留历史多份（每次生成一条新记录），页面上显示最近一份 + 历史列表。
- `summary.data_fingerprint` 存对比数据的摘要（人×日×数字的哈希）：**同区间 + 同数据 → 直接复用上一份报告，不重复烧 token**。

## 6. 服务层接口

### 6.1 `app/services/daily_report.py`

```python
JST = timezone(timedelta(hours=9))     # 固定 +09:00（不用 zoneinfo，避免镜像缺 tzdata）

def jst_today() -> date

def submit_report(db, user, *, area, p1_cnt, p2_cnt, client_ts="") -> StaffDailyReport
    # 校验：p1/p2 为 0..999 整数；已存在 (person_code, jst_today()) → raise AlreadySubmitted

def my_reports(db, person_code, *, month="") -> list[dict]      # 我的填报历史（按日倒序）
def list_reports(db, *, start=None, end=None, person_code="", page=1, per=50) -> dict
    # 管理端列表；附带"该区间应填天数 / 实填天数 / 漏填日期"（漏填 = 有系统记录但没填报）

def compare(db, start: date, end: date, person_code="") -> dict
    # 纯读 person_daily_stats + staff_daily_reports，程序计算，结构见 §7
def suggest_period(db) -> tuple[date, date]
    # 默认区间 = formal_records 最近一次入表的日期范围（min/max japan_date）
```

### 6.2 `app/services/report_ai.py`

```python
def report_ai_enabled() -> bool         # 配置开关（默认开）
def build_prompt(summary: dict) -> str  # 只喂汇总与异常点，不喂全量原始数据（控 token）
def start_analysis(db, user, start, end) -> StaffReportAnalysis
    # 建 pending 记录 → 后台线程执行；同 fingerprint 已有 done 报告则直接复用
def run_analysis(analysis_id: int) -> None   # 后台执行：running → 调模型 → done/failed
def retry(analysis_id: int) -> None          # failed → 重新执行
```

调模型统一复用 `app/services/ai_chat.py` 的 `chat()`（已带重试 + `reasoning_content` 兜底 + 未配置时回退）。
**未配置 AI / 开关关闭 / 调用失败 → 报告区显示「AI 报告暂不可用」，对比数字照常可看**（D12）。

### 6.3 配置项（`app/config.py`）

| 设置 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `report_ai_enabled` | `VISIT_REPORT_AI` | `1` | 0 = 关闭 AI 报告（对比功能不受影响） |
| `report_ai_max_tokens` | `VISIT_REPORT_AI_MAX_TOKENS` | `4000` | 报告长度上限 |
| `report_ai_timeout` | `VISIT_REPORT_AI_TIMEOUT` | `300` | 单次超时（秒） |

模型本身复用现有 `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL`（走我方 AI 网关）。

## 7. 对比口径（程序计算，不给模型算）

数据源：**系统侧** `person_daily_stats`（文件跑出来的：`ref_date`、`person_code`、`p1`、`p2`、`records`、`points`，**只读**）；**自报侧** `staff_daily_reports`。

**逐日比对**（人 × 日）：

| 情况 | 归类 | 展示 |
|---|---|---|
| 两侧都有 | `both` | 系统 p1/p2 ↔ 自报 p1/p2，差值 Δ1/Δ2/Δ总 |
| 系统有、自报无 | `missing_report` | **漏填报**（该员工那天没报） |
| 自报有、系统无 | `missing_system` | 自报无系统记录（文件没覆盖/未做/口径不同） |
| 两侧都无 | 不展示 | — |

**区间合计**（人 × 区间）：`系统 p1/p2 合计`、`自报 p1/p2 合计`、`Δ1点 / Δ2点 / Δ总`、**偏差率**（Δ ÷ 系统；系统为 0 时显示 `—`）、`实填天数 / 应填天数`。

**偏差方向**：
- `Δ = 系统 − 自报 > 0` → 员工**少报**（可能漏报/懒得记）；
- `Δ < 0` → 员工**多报**（可能把不是自己做的算进来，或与文件口径不同）；
- `|偏差率|` 排序 → 报告重点看头部几个人。

**边界**：区间内文件**尚未入表**（`person_daily_stats` 无数据）→ 页面提示「该区间还没入表，先上传并判定文件」，不生成空报告。

## 8. 大模型分析报告

**触发**：管理员在对比页选好区间 → 点「生成 AI 分析报告」→ 建 `pending` 记录 → 后台执行 → 页面轮询/刷新看 `status`。

**输入给模型的**（**只给汇总，控 token**）：
- 区间、人数、总店数对比（系统 vs 自报）
- 按偏差率排序的 Top N 人员（含 Δ1/Δ2/Δ总、实填/应填天数）
- 四类计数（`both` / `missing_report` / `missing_system` / 未填报人数）
- 逐日异常点（只挑 |Δ| ≥ 阈值的行）

**报告应包含**（写进 prompt）：
1. **总体一致性**（整体偏差率、趋势一句话）
2. **偏差排名**（谁最准、谁偏差最大）
3. **异常聚焦**（集中在哪几天、哪个区域）
4. **原因推断**（少报 / 多报 / 口径不同 / 文件缺数据 / 漏填报，分别指向谁）
5. **追问清单**（"某某 9/22 自报 12 家、系统 5 家，建议核实"这类可直接照着问的话）

**可靠性设计**（对应 D12，也是"敢上线"的前提）：
- **异步**：生成过程不占用管理员页面；状态 `pending/running/done/failed` 可见；
- **超时 + 重试一次**；失败写 `ai_error`，可一键重试；
- **缓存复用**：同区间 + 同数据指纹 → 直接复用已有报告，不重复消耗；
- **一键关闭**：`VISIT_REPORT_AI=0` 时只显示对比数字与"AI 报告已关闭"；
- **token 留痕**：每次生成记 `ai_model` 与 `ai_tokens`，随时能查消耗。

## 9. 页面与权限

**员工端**

| 路由 | 说明 |
|---|---|
| `GET /my/report` | 填报页（日期=今天、担当区域、1点数、2点数）+ 今日已填内容 + 我的历史 |
| `POST /my/report` | 提交（**普通表单 POST + 303 重定向**，不需要 JS；已填报 → 提示拒绝） |

**管理端**

| 路由 | 说明 |
|---|---|
| `GET /staff-reports` | 填报列表：区间/人筛选，列 日期·员工·区域·1点·2点·总店数·提交时刻·漏填标记；分页 |
| `GET /staff-reports/compare` | 对比页：逐人区间合计 + 逐日明细 + 四类计数 + 报告区 |
| `POST /staff-reports/compare/analyze` | 生成 AI 分析报告（建 pending → 后台跑）→ 303 |
| `POST /staff-reports/analysis/{id}/retry` | 重试失败的报告 |
| `GET /staff-reports/export?kind=reports\|compare` | Excel 导出（openpyxl，复用现有 `StreamingResponse` 写法） |

- 权限：`STAFF_ALLOWED` 加 `/my/report`（中间件 `path.startswith` 前缀匹配）；员工端校验 `role=="staff"` 且有 `person_code`；管理端 `role=="admin"`；中间件 + 路由双层。
- 未改密员工会被重定向到 `/my/password`（`must_change_password`），留意验收账号。
- 文案全部走 `app/i18n.py`（现场是日本员工，日文是必做项）。**报告正文由模型生成，不翻译**；页面框架文案要双语。

## 10. 从 v4 删掉的东西（作废，不再实现）

门头照与照片表、阿里云 OSS 与存储抽象、Google Maps / Places / 地图选点、GPS 与精度留痕、店名输入与店铺实体对齐（head master / 同名歧义）、打卡点位簿、每日多次打卡、赛马榜、`checkin_compare` 五类差异。

**连带结论**：`docs/谷歌地图API申请流程.md` 与 `docs/阿里云OSS开通清单.md` **本功能已不需要**（周一不必麻烦客户申请）。文档暂时保留，你要删我再删。

## 11. 口径默认值（如需调整说一声，都是一句话的事）

1. **不能补填、不能修改**：只填当天，提交后不可改（重复提交直接拒绝）。
2. **担当区域 = 手输自由文本**（最多 64 字）；若你给一份固定区域清单，我改成下拉 + 按区域汇总对比。
3. **报告手动触发**：上传文件后管理员在对比页点「生成 AI 分析报告」。
4. **默认区间**：取最近一次入表的日期范围，管理员可改。

## 12. 明确不做

防作弊、补填审批、与工资/绩效的任何挂钩、照片、定位、店名、赛马榜、MCP 工具、生产上线、自动定时生成报告。

## 13. 集成注意

1. **时区**：业务日 = JST，固定 `timedelta(hours=9)`（不用 `zoneinfo`）。
2. **模板循环变量不可命名为 `t`**（覆盖 i18n 全局函数）：用 `r` / `row`。
3. **`{# #}` 与 `<script>` 内中文不会被自动翻译**：本功能几乎不依赖 JS，问题不大。
4. **迁移 head**：`down_revision = "f7e8d9c0b1a2"`。
5. **SQLite/PG 双兼容**：Text 默认值用 `sa.text("('')")`；唯一约束用 `UniqueConstraint("person_code", "report_date")`。
6. **CSRF**：表单 POST 需带 `csrf_token`（复用 `csrf_ok`）。
7. **报告异步**：用后台线程（现有 `ai_batch.run_batch_task` 就是这么做的，照抄范式）；注意每线程自建 DB 会话。
8. **大模型只用来写报告**：所有数字由程序算好再喂给模型，**禁止让模型自己算差值**（算错会误导管理决策）。
