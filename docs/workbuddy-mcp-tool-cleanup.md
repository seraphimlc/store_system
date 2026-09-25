# 工具冗余清理计划（WorkBuddy 反馈 v2，2026-09-25）

> 来源：WorkBuddy 对 40 个工具的全量实测分析。已人工验证关键断言后形成本计划。
> 前提：等 A12（P0–P2 改造）落地后再执行本批次（同一批文件，避免并发冲突）。

## 已人工验证的断言

| 反馈断言 | 验证结果 |
|---|---|
| `perf_ranking` ⊆ `month_salary`（逐字段一致） | ✅ 属实（month_salary 全量含全部字段，ranking 仅降序取 N） |
| `month_summary` ⊆ `dashboard`（5 字段全等） | ✅ 属实（formal_rows=records / points_total=points / p1/p2/employees） |
| `dashboard` 内部 metrics ≈ company_summary | ✅ **属实**（实测：records/points/amount/employees/p1/p2 数值全等，键名不同；top_staff 又重复排行） |
| `upload_recon` ⊆ `upload_file(kind=recon)` | ✅ 属实（schema 对比：upload_recon 无 kind，其余同） |
| `export_recon_result` vs `export_recon_report` | ⏳ 待比对各 sheet 内容（report 是否含 result 全部） |

## 执行清单（按性价比）

1. **`month_salary` 加 `sort_by`（points/amount）+ `limit`（默认 0=全部）**；`perf_ranking` 描述标 deprecated（保留兼容，指向 month_salary）。
2. **dashboard 合并 `metrics`/`company_summary`**：保留 `metrics` 为主结构（字段更全），`company_summary` 不再返回（或返回时注明 deprecated）。`top_staff` 改为可选（`top=0` 不返回）。
3. **互斥指引（本批次核心，价最低）**：
   - 三「月汇总」工具描述首句各自注明：summary=只要汇总数字 / salary=要看每人明细 / dashboard=要经营总览·质量分析。
   - 三「查个人」工具注明：salary(person)=月汇总 / person_detail=日明细下钻 / payroll_rows(person)=发薪·找平维度。
4. **`month_summary` 描述注明**「dashboard 的精简版，需要更多指标请用 dashboard」。
5. **`upload_recon` 描述首句标注 deprecated**：「统一入口请用 visit_upload_file（自动识别，可 kind='recon' 强制对账）」。
6. **比对 `export_recon_result` vs `export_recon_report`**：若 report 已含 result 全部 sheet，result 标 deprecated。
7. 40 → 36~37 个工具，但**不强制删**（deprecated 优先，避免破坏既有调用）。

## 约束
- 不删既有返回字段（合并时保留主结构字段全集）；可新增 `sort_by`/`limit` 参数。
- 涉及文件：capability.py / read_ops.py / recon_write_ops.py / export_ops.py（等 A12 落地后）。
- 完成后跑 mcp 全量 + web 全量，重启服务验证工具清单。
