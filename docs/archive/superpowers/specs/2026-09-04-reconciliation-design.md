# 对账模块设计（Reconciliation Design）

- 日期：2026-09-04
- 状态：定稿草案（待用户审阅）
- 配套：设计 spec v0.4（`2026-09-04-store-settlement-web-design.md`）、页面交互清单、引擎验收附录 B
- 核心思路（用户提出）：**把月度账单拆成日/周粒度，差异驱动、逐块核对；差异数据导出 Excel 交付**

---

## 1. 目标与原则

对账 = **系统成绩 vs 外部账单**，按小粒度（日/周/月）逐步核对，只处理差异，导出 Excel 跟进。
- 简单：系统侧每日成绩在清洗时已物化（daily_system_points），对账只剩"比对 + 状态判定"，零额外计算。
- 快捷：默认只看差异；粒度可切换（日→周→月）；发现"日对不上但周对得上"= 补录/日期串位类，一眼可见。
- 可追踪：每条差异可"确认"打勾留痕；差异明细含系统侧与账单侧原始行，Excel 直接发出去跟进。
- 不猜测：匹配一律按人员编号；姓名只在有对照关系时辅助；匹配不上的行单独分族列报（同差异族 B-2/B-3 哲学）。

## 2. 两类对账

| | 类型 A：每日自报对账 | 类型 B：闫总月度参考对账 |
|---|---|---|
| 外部账单 | 员工交的每日自报 Excel | 闫总月度最终文件（如 0701-0731 的 Sheet5）|
| 粒度 | 日本日期 × 人员编号（可聚合为周/月）| 月 × Store ID |
| 键 | (japan_date, submitter_code) | (store_id) |
| 状态 | 一致 / 不一致 / 缺少自报 / 系统无记录 | 一致 / 系统有参考无 / 参考有系统无 / 双方有但保留行不同 |
| 差异细分 | 差额 = 系统点数 − 自报点数（需求书口径）| 差异族 B-1（名称空格变体）/ B-2（仅参考）/ B-3（仅系统）|
| 确认推进 | 逐（日×人）块确认 | 逐店确认 |

两套共用同一套"任务 + 结果 + 确认 + 导出"骨架，仅键与差异分类不同。

## 3. 数据流与表（扩展 spec §4.8）

```
[run 清洗完成]                     [上传账单 Excel]
      │                                  │
      ▼                                  ▼
daily_system_points          账单导入（列头可映射，样例到位前不锁死格式）
      │                        ├─ 类型A: self_report_rows (date, code, points[, 明细行按人汇总])
      │                        └─ 类型B: reference_rows    (store_id, name, …)
      └─────────► recon_tasks(kind: daily_report | monthly_ref,
                              run_id, source_import_id, status, created_by, summary)
                        │
                        ▼
                  recon_results(键×状态×差异×确认)
                        │
                        ▼
            对账页（差异驱动）+ 差异 Excel 导出
```

新表（Alembic 迁移在 Web/DB 计划中建）：
- `recon_tasks(id PK, kind ENUM, run_id FK, source_import_id FK, status(pending/running/done/failed), created_by, created_at, finished_at, params JSONB, summary JSONB)` —— 两种 kind 同表。
- `self_report_imports` / `self_report_rows(id, import_id, japan_date, submitter_code, submitter_name, reported_points, original_row JSONB, unresolved TEXT NULL)` —— 导入时做姓名→编号解析；解析不出 → unresolved 存原文、该行进"待映射"名单，**不静默丢弃也不猜**。
- `recon_results(id, task_id, key_japan_date DATE NULL, key_store_id TEXT NULL, submitter_code NULL, system_value NUMERIC, report_value NUMERIC NULL, diff NUMERIC, status ENUM, family TEXT NULL(类型B差异族), confirmed BOOL DEFAULT false, note TEXT)` —— 单表容纳两类（各自键列）。
- `recon_result_rows(id, result_id FK, side ENUM(system/report), ref JSONB)` —— 结果 ↔ 来源行链接（点开差异能看到系统当日店铺明细与账单原始行）。
- 类型 B 继续使用 spec §4.8 的 `reference_imports/reference_rows`；不再单设 reference_diffs，统一收敛到 recon_results（family 标 B-1/B-2/B-3）。

周/月视图**不建表**：由 recon_results 按 (ISO年-周 / 年月, submitter_code) 聚合即时得出（数据量小：人×日 ≈ 22 人×31 天级）。

## 4. 匹配与状态判定

### 4.1 类型 A（每日自报）
- 系统侧：该 run 的 `daily_system_points` 按 (japan_date, code) —— total_points。
- 账单侧：`self_report_rows` 同键求和 reported_points（若员工按店报 1/2 分明细行，导入时 `self_report_mode: total|detail` 可配，detail 自动按 (date, code) 加总）。
- 逐键判定：
  - 双方都有：`reported == system` → 一致；否则 → 不一致，diff = system − reported。
  - 只有系统 → 缺少自报（最常见，提醒员工补）。
  - 只有账单 → 系统无记录（当天系统没有该人 final，可能日期/编号错位，重点查）。
- **周聚合提示**：周内日级非一致但周和相等 → 标 `日期错位(对消)` 提示条，帮助快速定位串日/补录。
- 确认流：页面默认列 status ≠ 一致 的块，操作 `确认(一致或已处理)` 打勾 + 备注；支持"本周全部确认"。

### 4.2 类型 B（闫总月度参考）
- 系统侧：run 的 final 集合（引擎 trim/raw 双模式已可复现参考口径，spec 附录 B）。
- 账单侧：`reference_rows`（如 Sheet5 的 12,884 店）。
- 逐店差异分类算法（可用 7 月真实数据验证）：
  1. 双方同店 → 一致；比较保留行（Modified/Visible/Record ID）不同 → 双方有但保留行不同。
  2. 仅参考有 → 若系统侧存在**同 trim 店名**的另一店 → **B-1 名称空格变体**（附录 B.3-1；trim 模式下被合并）；否则 **B-2 仅参考**。
  3. 仅系统有 → 若参考侧存在同 trim 店名另一店 → B-1；否则 **B-3 仅系统**（含已核实的"疑似人工剔除"11 家，见附录 B.3-2，待万总裁定）。
- 状态语义与引擎 raw 模式的关系：对账**默认以书面规则（trim 开）**为系统口径，B-1/B-2/B-3 分族列报；裁决后可通过 rule params（name_mode）或豁免名单对齐，不改判历史。

## 5. 页面（阶段二，补充交互清单）

- `/reconcile`：对账任务列表（新建：选类型 + run + 账单文件；历史含 summary 与状态）。
- `/reconcile/{id}`：差异驱动主页面：
  - 顶部：任务元信息 + 状态计数卡（一致/不一致/缺自报/无记录 × 周进度）
  - 粒度切换：日（默认，仅非一致）/ 周 / 月 视图
  - 表格列（日粒度）：日期 / 人员(编号+姓名) / 系统点数 / 自报点数 / 差额 / 状态 / 确认 / 备注
  - 点击行 → 抽屉：系统当日明细（final 店列表或明细）+ 账单原始行（recon_result_rows）
  - 操作：确认勾选、备注、本周全确认、导出
- `/reconcile/{id}/unresolved`：自报导入时姓名→编号未匹配清单（显示原文，供管理员建别名/手工指定后重导该行）。
- 导出按钮 → §6 Excel。

## 6. 差异 Excel 导出

### 6.1 类型 A（自报）——默认只含差异
1. `总览`：周×状态计数 + 日对消提示条
2. `差异明细`：日期/人员编号/姓名/系统点数/自报点数/差额/状态/确认状态/备注 + 系统当日有效店数 + 账单原始行摘录
3. `对账矩阵(日)`：行=人员，列=日期，格=S/R/状态 简写（供人工快速扫）
4. `周汇总`：周×人员 系统合计/自报合计/差额/日级不一致数
5. `待映射名单`（如有）
- 全量版另存（含"一致"行）供归档。

### 6.2 类型 B（闫总参考）
1. `总览`：一致/仅参考/仅系统/保留行不同 + 差异族 B-1/B-2/B-3 计数
2. `差异明细`：Store ID/店名/系统 Modified/参考 Modified/Visible/Record ID/差异族/确认
3. `仅参考清单`、`仅系统清单` 两个 sheet（各自原始行）
- 文件名约定：`对账_<类型>_<期次>_<日期>.xlsx`。

## 7. 验证与验收（对账模块）

- 类型 A：合成自报样例 → 全状态判定正确；真实样例到位后校准列映射。
- 类型 B：用 7 月闫总文件做回归锚点——差异族算法应输出 trim 下 exactly missing 11/B-1 归类、extra 11/B-3；raw 下 B-1 消失（0 missing），extra 11/B-3 不变（与引擎验收一致）。
- 确认流与导出：端到端测试（建任务→比对→确认→导出→读回断言关键单元格）。

## 8. 仍待业务侧提供（不阻塞实现到系统侧）

1. 每日自报 Excel 真实样例（列头/是否含编号/按人总点或按店明细）——样例到位后定 `self_report_mode` 与列映射（设计已做成可映射，不锁死）。
2. 姓名→编号对照策略确认：默认自动用 persons 表最早写法匹配 + 未匹配待映射清单；官方名册（若有）直接覆盖。
3. 差异跟进闭环形态：确认打勾是否等于"结算依据"？Excel 回传的修正（如自报改值）是否需要回填系统（v1 对账只记录不修改系统数据，修订走重新清洗 run）。
4. 周口径：自然周（周一~周日）还是日本财年周/按月内 1-7/8-14 分段——默认 ISO 周一~周日，可配。
