# 巡店数据结算系统（Store Settlement）设计文档

- 日期：2026-09-04
- 版本：v0.4（草案，spec 第三轮评审修订）
- 关联输入：`~/Desktop/万总/巡店数据结算软件开发需求说明书.docx`（"需求书"）；万总文件夹 5 份 Excel：4 份巡店导出（附录 A）+ `闫总最终0701-0731.xlsx` 月度对账参考文件（附录 B）

---

## 0. 已确认的关键决策（来自需求对话）

| 决策点 | 结论 |
|---|---|
| 系统形态 | 小的 Web 系统：数据库保存，raw → 清洗 → 正式数据 → 分析统计 |
| 部署 | 云服务器，办公室内外多人浏览器访问 |
| 后端技术 | Python（FastAPI） |
| 登录 | 简单账号密码；v1 单角色（管理员）；员工角色表结构预留 |
| 每日自报 | 仍按需求书：员工交 Excel，管理员导入后对账（阶段二实现） |
| v1 交付顺序 | 先打通"导入 raw → 清洗 → 月度/人员/精算统计"，再补对账 |
| 月度口径 | 选文件组＝一个结算期（月）；每期一个清洗任务 |
| 7/9 分界 | 最终保留记录 Modified Time 日期 >= 2026-07-09 按新规，否则 1 点 |
| 字段识别 | 表头名去空白匹配，别名 `Deploy New A+POSM` ≡ `NEW A+ POSM`；缺失必需列即报错，不猜测 |
| 验证基准 | 系统按需求书书面规则实现；闫总月度文件（如 7 月 Sheet5）作为**对账参考**逐店比对，差异分族列报（附录 B），由万总裁定权威口径后再对齐 |

---

## 1. 背景与目标

需求书把"Excel 去重工具"升级为可长期使用的正式软件。本设计采用**数据库 + Web 分层**：Excel 只作为一次性输入被解析进 raw 表；清洗结果进入正式表；统计、对账、结算全部基于正式数据表；规则与每次运行结果留痕，可复查、可重算。

### 范围（v1）
1. 管理员登录，上传/导入巡店 Excel（多文件）。
2. 系统解析入库到 raw 表，展示解析诊断（格式、行数、警告、错误明细）。
3. 管理员为某结算期选择文件组，运行清洗任务（合并 → 去重 → 点数），结果写入正式表。
4. 查看月度总览、人员成绩（点击查看人员明细）、运行历史。
5. 导出结算 Excel（月度总览 / 人员汇总 / 个人精算 / 全量明细）。
6. 任务运行记录含规则快照与计数，可复查。

### 明确不做（YAGNI / 需求书 §9 范围外）
- 员工登录自报、多角色工作流（角色表结构预留，不做功能）
- 登录 SSO / 手机 App / 完整工资税务
- 自动从 MarsNavi 拉数据、云端协作/多组织
- 对账导入与差异明细页面（阶段二，schema 与路由预留；**对账参考文件比对**在开发验收期以脚本先行，见 §11.2/附录 B）

---

## 2. 总体架构

```
浏览器 (管理员, 多人)
   │ HTTPS
   ▼
[nginx 反代 + TLS]  ── 静态资源 / 反代 API
   ▼
[FastAPI 应用 (gunicorn+uvicorn, Docker)]
   ├─ Web 页面（Jinja2 + htmx + Alpine.js 轻交互）
   ├─ API/导入/清洗/导出 路由
   └─ 服务层:
        loader(解析Excel) → importer(写raw)
        cleaner(清洗任务: 合并/去重/点数) → 写正式表
        analytics(统计查询)   settle(精算)   exporter(Excel)
   ▼
[PostgreSQL 16 (Docker, 独立volume)]   [uploads/ 原始文件存档(只读使用)]
```

分层原则（需求书 §9：结果可复查、原始只读）：
- **raw 层不可变**：原始 Excel 每行原样入库，永不修改/删除（可追溯）。
- **清洗任务可重放**：规则变化 → 新建 task run 重算，旧 run 保留可对比。
- **分析只看正式层**：统计/结算从 clean 结果表聚合，不重复实现业务规则。

---

## 3. 技术选型

| 组件 | 选择 | 理由 |
|---|---|---|
| 后端 | Python 3.11+ / FastAPI | 与既有分析脚本、openpyxl 生态一致；去重算法可直接复用验证过的实现 |
| ORM/迁移 | SQLAlchemy 2.x + Alembic | 表结构版本化，服务器升级可控 |
| 数据库 | PostgreSQL 16 | 多人共享、JSONB、事务、并发任务 |
| 前端 | Jinja2 + htmx + Alpine.js（服务端渲染为主） | 表格/表单密集型管理页，避免前端构建链；后续要更炫再演进 |
| 解析 | openpyxl（只读/普通模式自适应）+ 自研健壮 loader | 需兼容两种表头、坏 dimension 元数据（附录 A.2/B.2） |
| 部署 | Docker Compose（app + db + nginx）；pg_dump 备份脚本 | 云服务器一键起 |
| 测试 | pytest + 合成 fixture（真实数据不入库/不进仓） | 规则边界全覆盖 + 真实文件验收脚本（§11.2） |
| 认证 | 服务端 session + 密码哈希（argon2） | v1 单管理员角色够用 |

---

## 4. 数据库设计

### 4.1 users（用户，v1 仅管理员）
`id PK, username UNIQUE, password_hash, display_name, role TEXT DEFAULT 'admin'`（预留 'staff'）, `is_active, created_at`

### 4.2 imports（巡店 Excel 文件）
| 列 | 说明 |
|---|---|
| id PK | |
| file_name, file_sha256 UNIQUE, file_size | 落盘存档名 = sha256，防重传 |
| stored_path | uploads/ 内相对路径 |
| uploaded_by FK→users, uploaded_at | |
| month_label TEXT NULL | 管理员给文件打的结算期标签（如 "2026-07"），可后改 |
| format TEXT | 探测结果：`wide50` / `flat9` / 其他（附录 A/B） |
| header_row, data_start_row | 探测到的表头行/数据起始行 |
| parsed_sheets JSONB | 实际入库的 sheet 名清单（含尾空格原文） |
| ignored_sheets JSONB | 忽略的 sheet（名称/行数/原因，如文件 4 的旧工具 Sheet1/2/4） |
| total_rows, parsed_rows | Excel 数据总行数（主 sheet）/ 成功入库行数 |
| status | `uploaded → parsed / failed` |
| warnings JSONB | 解析警告数组（忽略的 sheet、可见列无 NO 等需人工确认项；**平局属 run 级提示**，写入 run.summary，见 §4.5）|
| errors JSONB | 解析错误数组（缺列/时间无法识别/值域异常 → failed 并列出原因） |
| created_at | |

### 4.3 raw_records（raw 层，逐行原样）
| 列 | 说明 |
|---|---|
| id PK | |
| import_id FK→imports, sheet_name, excel_row | UNIQUE(import_id, sheet_name, excel_row) |
| store_id_raw, store_name_local_raw, store_name_en_raw | 原文，不 trim |
| modified_raw TEXT, first_visit_raw TEXT, review_completion_raw TEXT | 时间列原文 |
| submitter_raw TEXT | 提交人原文 `姓名(编号)` |
| submitter_code TEXT NULL FK→persons.code | 导入时正则 `(.+)\((\d+)\)$` 提取（去首尾空白后）；无法解析为 NULL（§9"未识别"） |
| record_id_raw TEXT | Record ID 原文（宽表有、简表/闫总简表为 NULL 或缺列） |
| visible_raw, existing_visible_raw, deploy_raw | YES/NO/空白 原文 |
| original_row JSONB | 整行原值数组（防丢字段，兼容列序变化） |
| created_at | |

> raw 不可变，解析分工明确（**导入时** vs **每次 run 时**）：
> - **导入事务内完成并落列**：submitter_code 提取（persons 需在导入期建立）；modified_raw 等时间文本的**格式校验**（非 `YYYY-MM-DD HH:MM:SS` 即该文件 failed，绝不猜测）；visible/deploy 值域校验（去首尾空白后**仅接受精确的 YES/NO/空白**，不做大小写折叠——`Yes`/`yes` 等变体一律按值域异常使文件 failed 并列出首个异常行号，避免把未确认变体当 YES 猜测，与 §9 一致）。
> - **每个清洗 run 内完成**：从 `modified_raw` 按该 run 的 rule_version 确定性解析出 `modified_at`/`japan_date`（写入 clean_records）。raw 只存文本、不落解析时间 → 将来时间规则升级只需新 rule_version 重跑，无需重新上传（与 §5.5 重放语义一致）。
> - `original_row` 保证将来需要新字段时无需重新上传。

### 4.4 persons（人员）
`code PK（编号字符串，如 2188240620009615）, display_name, first_seen_import_id FK→imports, created_at`
- 编号唯一、只增不改。同一编号出现多种姓名写法（简繁/全角空格/拼音）时，`display_name` = 该编号在 raw 中**最早记录**（按 import_id, excel_row 序）的写法，策略确定性、重建可复现；官方名册到位后可在页面覆盖显示名（覆盖值存 `display_name_override`，不做入 v1）。

### 4.5 task_runs（清洗任务 = 一个结算期的处理）
| 列 | 说明 |
|---|---|
| id PK | |
| label TEXT | 期次名（如 "2026-07"）|
| boundary_date DATE | 分界日（默认 2026-07-09；>= 当天新规）|
| rule_version TEXT | 规则包版本（代码 git 短 hash + 语义版本）|
| input_import_ids JSONB | 本任务合并的文件（快照）|
| params JSONB | 规则参数快照：字段别名映射、去重顺序、平局整序、**店名归一化开关（默认 trim，附录 B 口径开关）**、边界日等 |
| status | `pending / running / done / failed` |
| created_by FK→users, created_at, started_at, finished_at | |
| summary JSONB | 运行审计缓存：各 bucket 计数 + 全员精算额 + 未识别行数 + **平局组数 + 全空键行数**（**同一聚合函数**写 person_stats 与 summary，防双码漂移；展示以 person_stats/settle_amounts/clean_records 查询为准）|
| note TEXT | 管理员备注 |

### 4.6 clean_records（正式数据层核心：逐行判定）
| 列 | 说明 |
|---|---|
| id PK | |
| run_id FK→task_runs | |
| import_id FK, raw_record_id FK→raw_records | 回链 raw（原行可查）|
| store_id TEXT, store_name TEXT | **展示快照**：一律存去首尾空白后的文本（空键存空串 `''`，NULL 仅当原单元格缺失）；**判重键**由 run 参数决定——trim 开时按本列值比较，trim 关时店名比较用原始文本（附录 B-1，两行快照相同但可被保留为不同店）|
| modified_at TIMESTAMP | 该 run 由 modified_raw 解析（日本时间 naive，见 5.5）|
| japan_date DATE | = modified_at 的日本日历日（物化，每日成绩/对账按它分组）|
| submitter_code TEXT NULL FK→persons.code | 可能为 NULL（未识别）|
| visible, deploy | ENUM('YES','NO')，可空（NULL=空白；导入时已做值域校验，run 内不再出现其他值）|
| bucket | ENUM(`final` / `dup_by_id` / `dup_by_name` / `visible_blank`) |
| dup_of_id BIGINT NULL | 见下方"淘汰引用"语义 |
| dup_earliest JSONB NULL | dup_of_id 指向行的快照（submitter_code / modified_at / japan_date / file_name / excel_row / bucket）|
| created_at | |

**淘汰引用（dup_of）语义**：
- `dup_by_id` 行：dup_of = 同 Store ID 组内最早那条（= 直接淘汰它的 A 幸存者）。该 A 幸存者**可能随后又被店名规则淘汰**（A 列重复行的 dup_of 可指向非 final 行，允许链）。
- `dup_by_name` 行：dup_of = 同店名组内最早的 A 幸存者（必然是 final 行——见 §5.1 步骤 5 的性质）。
- `final` 与 `visible_blank` 行：dup_of_id 与 dup_earliest 恒为 NULL（visible_blank 从未进入判重，无"最早保留者"）。
- **dup_earliest 语义**：一律是 dup_of_id 指向的**直接淘汰者**的快照（不是链上更上游）。链的典型三行例（X dup_by_id → Y dup_by_name → Z final）：X.dup_of=Y 且 X.dup_earliest=Y 的快照；Y.dup_of=Z。页面展示 dup 行的"原因"时，若其 dup_of 自身非 final（如 Y），则沿 dup_of 继续展示 Y 自己的原因（去向链一目了然）；X 的 dup_earliest 不冒充"最终保留者"。
- 索引：`(run_id, bucket)`、`(run_id, submitter_code, bucket)`、`(run_id, submitter_code, japan_date)`、`(run_id, japan_date)`、`(run_id, dup_of_id)`、`(run_id, modified_at)`。

### 4.7 聚合表（每 run 物化；单一来源 = clean_records）
- `person_stats(run_id, submitter_code, raw_submitted, visible_blank, dup_count, final_valid, points_1, points_2, total_points, settle_amount)` — 人员成绩 + 精算额。**未识别（code=NULL）单独一行**，计入全局汇总，不并入任何人。
- `daily_system_points(run_id, japan_date, submitter_code, final_stores, points_1, points_2, total_points)` — v1 即物化：人员明细页"每日成绩"与**导出 sheet 4（每日系统成绩）**直接消费；阶段二对账直接复用，不再新建系统点来源。
- `settle_amounts(run_id, submitter_code, total_points, full_68_groups, remainder, amount)` — 精算明细；**1点店数/2点店数/姓名**由 person_stats/persons 联表取（个人精算导出 = settle_amounts ⋈ person_stats ⋈ persons）。
- 月度总览**不建表**：指标来源分工——A列重复/B列重复等**桶计数**查 clean_records `(run_id, bucket)`；人员相关与精算额由 person_stats/settle_amounts 聚合；run.summary 为同函数审计缓存。**人员汇总/个人精算导出必须包含"未识别"行（姓名显示"未识别"）**，保证导出合计与总览/精算总额可核对（禁止内连接静默丢行）。每 run 数据血缘：raw_records →（run 清洗）→ clean_records → 上述三表，禁止其他写入路径。

### 4.8 对账预留（阶段二）
> 对账模块详细设计见《2026-09-04-reconciliation-design.md》：两类对账（每日自报 / 闫总月度参考）、日/周/月粒度、差异族 B-1/B-2/B-3、确认流与差异 Excel 导出。以下为 schema 骨架，以该设计文档为准。
- `reference_imports(id, file_name, sha256, month_label, uploaded_by, uploaded_at, status, warnings/errors JSONB)` — 闫总式月度对账参考文件导入（阶段二页面化；开发期脚本先行，§11.2）。
- `reference_rows(id, reference_import_id, store_id, store_name, modified_raw, submitter_raw, record_id_raw, visible_raw, deploy_raw, yz_points, original_row JSONB)` — 参考文件去重后行（如 Sheet5）。
- `reference_diffs(reference_import_id, run_id NULL, diff_type, store_id, sys_side JSONB, ref_side JSONB, note)` — diff_type ∈ 系统有参考无 / 参考有系统无 / 双方有但字段或保留行不同。
- `self_report_imports/self_report_rows/reconciliations`：`reconciliations(run_id, self_report_import_id, japan_date, submitter_code, system_points←daily_system_points, reported_points, diff, status)`，status ∈ 一致/不一致/缺少自报/系统无记录。

---

## 5. 核心业务规则（与需求书一致，冲突处按已确认决策）

### 5.1 合并与去重流水线（固定顺序）
对所选文件组（= 一个结算期）：
1. **合并**：所选文件组中**所有已解析 sheet 的行**（§5.2.1 匹配 STORE_TASK_EXCEL_SHEET 者；同文件多匹配 sheet 全部并入）→ 候选池。
2. **Visible 过滤**：`A+ POSM Visible` 非空（YES/NO 都保留）进候选；空白记录直接归桶 `visible_blank`，**不参与去重、不判重、dup_of 恒 NULL**（需求书：空白单独列）。空白可见行的 ID/店名即使与 final 相同，也只列在 visible_blank，不再判"重复"。
3. **排序**：按 `modified_at`（解析后的数值时间）升序。**平局整序**：`(modified_at, import_id, sheet_name, excel_row)`——时间相同按文件上传先后、sheet 名、行号稳定取先，保证全序确定；平局组数写入 run.summary，运行历史可见。
4. **A 列去重（Store ID）**：**只对 trim 后非空 ID 判重**——同 ID 只保留最早一条，其余归桶 `dup_by_id`（dup_of = 该 ID 最早一条）；**空白 ID 行直接跳过本步，空白 ID 之间永不互判重**。
5. **B 列去重（Store Name-Local）**：**只在第 4 步幸存者上执行**，且只对非空店名判重——同店名只保留最早一条，其余归桶 `dup_by_name`；空白店名行跳过，空白店名之间永不互判重。**店名归一化**：默认对店名先 trim 再比较（需求书书面规则）；归一化开关参数化（rule params，附录 B 显示参考文件实际未 trim，差异族 B-1）。
6. **桶划分完备性**：每条候选行（Visible 非空）恰好落入一个桶：`dup_by_id` / `dup_by_name` / `final`（`dup_by_id` 行不再参与 B 判重，两个重复桶互斥；第 5 步只作用于第 4 步幸存者，保证这一点）。
7. **性质与后果**（设计确认）：
   - B 步保留的店名最早者（该店名组内最早的 A 幸存者）此后不可能再被淘汰 → `dup_by_name` 行的 dup_of 必为 final。
   - B 去重可把某个 Store ID 的 A 幸存者整行淘汰（其店名与更早的另一 ID 行同名）→ 该 Store ID 不出现在最终有效。这是需求书"固定顺序"的应有语义；与参考输出对照见附录 B（差异族 B-2/B-3）。
   - 一条同时 ID、店名都空白的候选行会一路幸存进 final（空键间不互判重）——允许，但计入 warnings 供人工抽查（真实数据未见此类行）。
8. **重复记录不删除**：全部写入 clean_records，带 bucket、dup_of、dup_earliest（dup_of 直接淘汰者快照：谁提交/何时/哪个文件/行号）。

**不变量（每次 run 事务内断言，失败即回滚标 failed）**：
- 全局：`原始总数 = visible_blank数 + dup_by_id数 + dup_by_name数 + final数`
- 全局：`points_1店数 + points_2店数 = final数`；`总点数 = points_1店数 + 2 × points_2店数`
- 人员级（按 submitter_code）：`该人原始提交 = 其visible_blank + 其dup + 其final`
- **Submitter 无法解析的行**（§9"未识别"）计入全局"原始总数"，但不属于任何人——人员级不变量不覆盖它们；系统单独计数"未识别行数"并在总览/审计中显示，防止不变量误报。

### 5.2 字段识别（loader）
- 定位表头行：前三行内找值为 `Store ID` 的单元格（宽表在第 2 行、单表头表在第 1 行）。
- 列按表头名去空白后精确匹配；必需列（缺失 → 该文件 failed，错误上 UI，不猜测）：
  `Store ID`、`Store Name-Local`、`Modified Time`、`Submitter`、`A+ POSM Visible`
- Deploy 列（必需）：别名 `Deploy New A+POSM` ≡ `NEW A+ POSM`（需求书：旧文件兼容；附录 B 文件即用旧名）。
- 可选识别列（缺失不报错，值为 NULL）：`Store Name-English` → store_name_en_raw；`First Visit Time` → first_visit_raw；`Review Completion Time` → review_completion_raw；`Existing A+ POSM Visible` → existing_visible_raw；`Record ID` → record_id_raw。其余列一律只进 original_row JSONB，不承诺识别。
- 值域与时间校验：见 §4.3 注（导入期校验；异常 → 文件 failed 并列出首个异常行号，绝不猜测）。

**5.2.1 多 sheet 策略（关键）**：只解析 sheet 名去首尾空白后等于 `STORE_TASK_EXCEL_SHEET`（不区分大小写）的 sheet（MarsNavi 导出模板名）；同文件多个匹配 sheet 时全部入库并进候选池（警告注明）。**其余任何 sheet 一律忽略**并在 `ignored_sheets` 记警告（名称/行数/原因）——文件 4 的旧工具 Sheet1/2/4 与闫总文件的 `Sheet5`（对账结果表）即因此不进巡店 raw 池。若匹配 sheet 不存在，回退到"首个含全部必需列的 sheet"并警告；仍无 → 文件 failed。

### 5.3 1点/2点（基于最终保留记录）
- 取每条 final 记录自身的 `Deploy New A+POSM` 与 `japan_date`：
  - `japan_date < boundary_date`（默认 2026-07-09）→ **1 点**（无论 Deploy）。
  - `japan_date >= boundary_date` → Deploy=YES → **2 点**；NO 或空白 → **1 点**。
- 1点/2点**不参与去重**（去重永远保留 Modified 最早，需求书 §3）。
- 旧字段名 `NEW A+ POSM` 值语义与现列一致（YES/NO/空白）。

### 5.4 人员归属
- 每条 final / dup / visible_blank 记录归其 `submitter_code`（导入时从 Submitter 解析）。
- 姓名展示取 persons.display_name（按 §4.4 策略）；**聚合一律按编号**（简繁/全角空格/拼音不影响正确性）。

### 5.5 时间口径
- 源数据 Modified Time 为定宽文本 `YYYY-MM-DD HH:MM:SS`（日本时间 JST，无时区偏移）。**排序一律按 run 内解析的数值时间**（modified_at），不用文本序。
- `japan_date` = modified_at 的日历日（日本日期，无时区换算；如未来出现带时区/非 JST 数据再升级规则版本并重跑）。
- 每日成绩/对账按 japan_date 分组（需求书：按 Modified Time 的日本日期）。

### 5.6 精算（结算金额）
- 每满 68 点 = 20,000円；余数每点 = 250円。
- `amount = (total_points // 68) * 20000 + (total_points % 68) * 250`；`full_68_groups = total_points // 68`；`remainder = total_points % 68`。
- 校验样例（需求书）：67→16,750；68→20,000；69→20,250；136→40,000；146→42,500。作为单元测试断言。

---

## 6. 处理流程与任务模型

1. **导入**：管理员上传 .xlsx（大小上限 50MB，仅 .xlsx；sha256 防重）→ 后台任务解析（每文件一个事务）→ 写 `imports` + `raw_records` + persons 补全，逐文件给出诊断（格式/行数/忽略 sheet/警告/失败原因）。
2. **期次标记**：文件列表页可为文件设置/修改 `month_label`（导入时按文件名规则或日期范围给出建议，可改）。
3. **清洗**：新建 task run（按期次自动带上该期文件清单，或手动勾选）→ 后台执行 §5.1–5.6 → **单事务**写 clean_records + person_stats + daily_system_points + settle_amounts + summary → 不变量断言失败则 run 标 failed 并整体回滚，禁止半成品数据。
4. **复查/重算**：任何 run 的结果、规则参数、输入文件清单都留痕；规则升级（含店名归一化开关调整）后新建 run 重算，旧 run 保留可对比。

---

## 7. 页面与路由（v1）

| 路由 | 页面 | 内容 |
|---|---|---|
| `/login` | 登录 | 用户名密码 |
| `/` 仪表盘 | 概览 | 最近文件、最近 run、期次快捷入口 |
| `/files` | 巡店文件 | 上传（拖拽/选择）、文件列表、解析诊断（含 ignored_sheets）、月份标记 |
| `/runs` | 清洗任务 | 新建 run（选期次/参数含店名归一化开关）、运行历史（规则版本/计数/状态/**warnings 摘要：平局组数、全空键行数**）|
| `/runs/{id}/overview` | 月度总览 | 原始/可见非空/可见空白/A列重复/B列重复/最终有效/1点/2点/总点数/全员精算额/未识别行数（需求书 §4 九宫格 + 审计行）|
| `/runs/{id}/persons` | 人员成绩 | 姓名、原始、可见空白、最终有效、重复、1点、2点、总点数、精算金额；点击行 → 人员明细 |
| `/runs/{id}/persons/{code}` | 人员明细 | 有效店铺表 / 重复店铺表（带原因、dup_of 引用及其去向）/ Visible空白表 / 每日成绩（daily_system_points）|
| `/runs/{id}/duplicates` | 重复明细 | 全量重复（bucket/原因、dup_of 是谁、dup_earliest 直接淘汰者快照）；dup_of 非 final（A 链）时沿链展示其自身原因 |
| `/runs/{id}/export` | 导出 | 一键导出结算 Excel（§8 结构）|
| `/reconcile`（阶段二）| 对账 | 见对账设计文档：任务列表 / `/reconcile/{id}` 差异主页面（日·周·月粒度、确认）/ `/reconcile/{id}/unresolved` 待映射名单 |

---

## 8. 导出 Excel 结构（需求书 §8）

一个工作簿，sheet 布局：
1. `月度总览`：一行指标 + 规则快照文本（分界日、文件清单、rule_version、运行时间）
2. `人员汇总`：编号、姓名、原始、可见空白、最终有效、重复、1点、2点、总点数、精算金额
3. `个人精算`：编号、姓名、1点店数、2点店数、总点数、整68组数、余点数、金额（settle_amounts ⋈ person_stats ⋈ persons）
4. `每日系统成绩`：日期×人员（final_stores/1点/2点/总点数）
5. `最终有效`：全字段明细
6. `全部重复`：bucket/原因/dup_of 引用/dup_earliest 直接淘汰者快照/时间/原始文件/行号
7. `Visible空白`：全字段明细
8. `个人明细`：**单 sheet**，每行一条有效/重复/空白记录，带人员编号列（v1 不做"每人一个 sheet/文件"，避免 sheet 数无上限）

导出用 openpyxl 写内存后流式返回，样式简单（表头加粗、冻结首行）。金额单位円。

---

## 9. 错误处理与边界（汇总）

| 场景 | 行为 |
|---|---|
| 非 .xlsx / 损坏文件 | 上传或解析失败，UI 红字说明，不影响其他文件 |
| 缺必需列 / 无匹配 sheet 且回退 sheet 也缺列 / Modified 无法解析 | import failed，errors 列出文件+原因，绝不猜测 |
| Visible/Deploy 出现非 YES/NO/空白的值（值域异常）| import failed，列出首个异常行号与值，绝不猜测（如遇 'Yes'/'是' 等变体需先由业务确认映射再放开）|
| 超大文件 / 重复上传（sha256 相同）| 上限 50MB；相同 sha256 提示已存在 |
| 导入中途失败 | 单文件单事务，整体回滚，imports 标 failed |
| 清洗中断/不变量不满足 | run failed + 事务回滚 |
| 时间文本格式非预期（非 `YYYY-MM-DD HH:MM:SS`）| 该文件 failed（只认 19 位定宽，见 §5.2）|
| 平局 | 按 §5.1-3 全序稳定取先；平局组数写入 run.summary，在运行历史（warnings 摘要）可见 |
| 空店名/空 ID | §5.1-4/5 规则：空键间永不互判重；明细中空值行正常展示；全空键候选行进 final 但计入 warnings |
| 人员编号解析失败（Submitter 无括号编号）| import 警告；该行入库、submitter_code=NULL 归"未识别"，总览单独计数展示，不静默并入他人 |
| 多余 sheet（旧工具残留、对账结果表等）| 忽略 + ignored_sheets 警告（§5.2.1），不导致文件失败 |

---

## 10. 安全与运维

- 部署：云服务器（Ubuntu）→ Docker Compose：`web`(gunicorn, 非 root)、`db`(PG16, volume 持久化)、`nginx`(TLS 终止, HTTP→HTTPS)。
- 密码 argon2 哈希；session cookie HttpOnly + SameSite=Lax；CSRF 令牌（htmx/表单）；登录防爆破（失败计数+延迟）。
- 上传目录只读给运行用户；raw 文件存 sha256 名；定期 `pg_dump` + uploads 打包到对象存储/异地（备份脚本交付）。
- `.env` 管理密钥（SECRET_KEY/DB_PASSWORD），`docker-compose.yml` + `deploy/README.md` 说明：域名解析、HTTPS 证书（nginx+certbot）、升级（alembic migrate + compose pull/up）、备份恢复演练。
- 审计：imports/task_runs 记录操作人；日志落盘轮转。

---

## 11. 测试与验收

### 11.1 单元（合成 fixture，仓库内）
- loader：双表头/单表头（两种列序简表）/尾空格 sheet/坏 dimension/别名列（Deploy vs NEW A+ POSM）/多 sheet 忽略/缺列报错/时间解析失败/值域异常/未识别提交人。
- pipeline 规则矩阵：可见 YES/NO/空白；同 ID 多记录取最早；同店名不同 ID；空店名/空 ID 各自不互判重；7/9 前后；Deploy 三态；跨文件最早；同刻平局（含跨 sheet 同行号）；A 链（A 幸存者被 B 淘汰时 dup_by_id 行的 dup_of 指向非 final）；桶互斥完备性；店名归一化开关（trim 开/关两档输出差异）。
- settle：5.6 样例断言 + 边界（0 点、67、68、69、146）。
- 不变量断言函数（全局 + 人员级 + 未识别排除）。
- 参考口径复现用例：用附录 B 已核实的数量（trim 关：12,895=参考 12,884+11；trim 开：12,884 但 11/11 成员互换）做回归锚点。

### 11.2 真实文件验收（仓库外数据，脚本读取本地路径，结果不入库/不入仓）
- 7 月验收：导入 4 份巡店文件（或闫总文件 `1-31原始` 主 sheet），按书面规则（trim 开）跑 run；与 `闫总最终0701-0731.xlsx` 的 Sheet5（12,884 行）做**逐店（Store ID）差异族报告**：
  - 预期总行数吻合 12,884；差异分族列报：B-1 店名空格变体（trim 语义差）、B-2/B-3 残余 11 行差（原因待万总裁定，见附录 B.4）。
  - **锚点口径（可复现）**：12,884/12,895 锚点仅用"闫总文件 `1-31原始` 中 Modified∈2026-07-01..31 且 Visible 非空的 13,879 行"→ ID 去重 → 店名去重（trim 开：12,884 但 11/11 成员互换；trim 关：12,895=参考+11 多余）。4 份巡店文件全量组只用于链路/不变量验证，不参与 12,884 锚点比对。
- 抽查 20 条最终有效 + 20 条重复 + 10 条空白，与 Excel 原行逐条比对（行号/字段/归属）。
- **验收标准（需求书 §9"逐条核对一致后再交付"）**：书面规则口径经万总确认后，与万总确认的月度结果逐条对比 100% 一致方可交付；确认前交付物 = 自检报告 + 差异族报告 + 明细导出供人工核对。

### 11.3 端到端
- 测试容器内起 app+db：登录 → 上传合成文件 → 建 run → 断言页面/API 计数与 DB 一致 → 导出 Excel 重新读回比对关键单元格。

---

## 12. 开发前仍需从业务侧获得（开放问题，不阻塞 v1 开发）

1. 云服务器具体信息（厂商/域名是否已有/是否需要我方写部署脚本即可）。
2. `闫总最终0701-0731.xlsx` 是否即"每月对账用的已确认结果"？每月是否都会提供同款文件（含 Sheet5 去重结果与 `作业分` 列）？其产生工具/流程是什么（决定参考文件能复现到何种程度）。
3. 每日自报 Excel 样例（阶段二需要；含日期/姓名或编号/自报总点数）。
4. 人员姓名与编号对照表是否官方提供（当前从数据自动建立，姓名展示策略见 §4.4）。
5. 期次（月）边界如何贴业务：文件窗口跨月（0726-0805）时按 Modified 日期归属月份还是人工指定归属——v1 采用"管理员为文件打 month_label + run 可手动勾选文件"两者结合，足以覆盖。
6. 文件 3（0706-0715）Visible 无 NO 是否正常（附录 A.3）。
7. **店名口径**：需求书写"店名去重前清除首尾空格"，参考输出显示未 trim（`'Ray-Ban '` 与 `'Ray-Ban'` 两行都算有效）——以哪个为准？（v1 默认按书面规则 trim，差异族 B-1 列报）
8. **11 行残余差异**（附录 B.4）与 Sheet5 `作业分` 列全空的原因/用途（是否人工评分/对账后补填）——请万总确认，作为对账比对口径。

---

## 附录 A：真实数据学习事实（4 份巡店导出文件）

### A.1 文件总览
| 文件 | 创建/修改 | Modified 覆盖 | 数据行 | 唯一StoreID | 格式 |
|---|---|---|---|---|---|
| 20260706-0715_..._MarsNavi.xlsx | 2026-07-16 / 09-04 | 07-03~07-15 | 6,325 | 4,258 | 宽50 |
| 0716-25 MarsNavi_...xlsx | 07-28 | 07-16~07-25 | 8,000 | 3,976 | 宽50 |
| 0726-0805_MarsNavi_...xlsx | 08-07 / 09-04 | 07-26~08-06 | 9,700 | 4,837 | 宽50 |
| 202607063328STORE_VISIT_RECORD.xlsx | 07-06 / 07-19 | 04-28~07-05 | 13,932(主表) | 6,496 | 简9 + 旧Sheet1/2/4 |

### A.2 格式事实（loader 必须兼容）
- 宽表：第 1 行分组标题、第 2 行字段名、数据第 3 行起；50 列。简表：单表头、数据第 2 行起；列序有差异（文件 4 无 Record ID、Submitter 在 F；闫总简表含 Record ID、Submitter 在 E——**必须按表头名识别**，见 B.2）。
- 主 sheet 名带尾空格 `STORE_TASK_EXCEL_SHEET `（各文件主表均为该模板名）。
- 文件 2/3 内部 `<dimension ref="A1"/>` 错误 → openpyxl read_only 截断到 1 行；需普通模式或按 XML 行流读。
- 宽表关键列：A Store ID / B Store Name-Local / G Modified Time / J Submitter / L Record ID(每行唯一) / AF A+ POSM Visible / AH Deploy New A+POSM。简表（文件 4）：A/B/C/D(Modified)/F(Submitter)/G(Visible)/I(Deploy)。
- Modified Time 全部为定宽字符串；无缺失。

### A.3 数据特征
- 全文件 Visible 仅 YES/NO/空白；**文件 3（0706-0715）Visible 无 NO**（YES 2,007/空白 4,318），需业务确认是否正常。
- 空白 Visible 与空白 Deploy 高度重合。
- 提交人格式 `姓名(编号)`，含全角空格（`平石 悠樹人`）、简繁混用（陈嘉溢/陳偉鋒）、拼音名（Jing Feiran）→ 统计按编号。
- 重复形态：同 Store ID/店名多条提交（同店不同 Record ID），非整行复制。

### A.4 文件 4 旧 tab 对照（参考性质）
- 文件 4 的 Sheet1/2/4 为旧工具阶段输出：2,945 / 3,333 / 6,233 行（Store ID 全唯一）。
- 按书面规则流水线对文件 4 主表模拟：Visible 非空 6,889 → ID 去重 6,409 → 店名去重 **6,244**（Sheet4 = 6,233，差 11 行，规律同附录 B 的店名/空白口径差异族）。仅作参考，不作验收基准。

---

## 附录 B：闫总月度对账参考文件（闫总最终0701-0731.xlsx）学习结论

### B.1 文件结构（Apache POI 生成，2026-07-06 创建 / 08-21 修改）
- `1-31原始`：28,293 数据行 × 9 列（**单表头**）。列：Store ID / Store Name-Local / Store Name-English / Modified Time / Submitter / Record ID / A+ POSM Visible / Existing A+ POSM / **NEW A+ POSM**（旧字段名，需求书点名要兼容）。Modified 覆盖 2025-11-25 ~ 2026-07-31（含大量历史重复行）；Visible YES 6,481 / NO 7,469 / 空白 14,343；唯一 ID 13,609。
- `Sheet5`：12,884 行 × 10 列，首列 `作业分`（当前**全空**），其余为去重后结果行：Store ID / 店名 / Modified（全在 2026-07-01~31）/ Submitter / Record ID / Visible（YES 5,763 / NO 7,121，**无空白**）/ NEW A+ POSM。行内 Store ID 全唯一。

### B.2 loader 兼容性要点（追加到 A.2）
- 该文件主 sheet 也叫 `STORE_TASK_EXCEL_SHEET `（尾空格），但为**单表头、9 列且含 Record ID、Submitter 列位在 E**——与文件 4 简表（无 Record ID、Submitter 在 F）不同 → 再次确认：**loader 一律按表头名定位列，绝不按列号猜**。`Sheet5` 非模板名 → 按 §5.2.1 忽略（对账时另行解析）。

### B.3 与书面规则的口径差异（重要，需万总裁定）
1. **差异族 B-1（店名 trim）**：Sheet5 中存在店名仅差首尾空格的"同名"多行（如 `'Ray-Ban '` 与 `'Ray-Ban'`、`' CAFE de CRIE'` 与 `'CAFE de CRIE'`、`'喫茶室ルノアール\u3000'` 与 `'喫茶室ルノアール'` 等 20 处）**均被保留** → 参考输出**未按需求书"trim 后判重"执行**（用原文判重）。复现验证：trim 关 → 12,895 行（= 参考 12,884 + 11 多余）；trim 开 → 12,884 行但 11/11 成员互换。
2. **差异族 B-2/B-3（11 行残余）**：trim 关时仍有 11 行系统保留而参考缺失（如 Ozzoneste、SHIMA AOYAMA PEAK、TOMMY HILFIGER、Pizza Slice 等）。这 11 店均为"7 月内先有 Visible 空白记录（早数秒~数十秒）、后有 YES/NO 记录"，其行与 Record ID 在参考中均不存在；在其余 4 份文件中无更早可见记录可解释；空白参与 ID 去重的假设与总量矛盾（按该假设仅剩 326 店）。**原因未知**（疑似旧工具内部处理或人工剔除，占 0.085%），不作猜测、列报待裁。
3. `作业分` 列全空：用途待确认（疑为对账/评分后人工填写；或该文件尚未完成"作业分"赋值流程）。

### B.4 对账用法（写入 v1 验收与阶段二设计）
- 参考文件 Sheet5 按 Store ID 与系统 run 的 final 集合比对，diff_type 分"系统有参考无 / 参考有系统无 / 双方有但字段差"；差异按 B-1（trim）、B-2/B-3（残余 11）分族呈现，不静默吞掉。
- 万总裁定权威口径（开放问题 12-7/8）后，通过 rule params 开关对齐；在裁决前系统默认按需求书书面规则（trim 开）计算并出具差异族报告。
