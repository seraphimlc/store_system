# MCP 工具场景化重构设计稿（49 → 16）

> 起因：用户指出「40+ 个工具根本用不上那么多」「不应该一个 RESTful API 就做一个工具，
> 要按实际场景来」。生产审计实测：**49 个工具里只有 13 个被调用过**。
>
> 本文档 = 设计稿，**待确认后再动代码**。

---

## 一、问题诊断：现在是"接口粒度"，不是"场景粒度"

| 症状 | 例子 |
|---|---|
| 工具名是**内部实现语言** | `store_skip_pair`、`rebuild_preview`、`recon_adjust_state`、`file_layout` |
| 一件事拆成**多个工具** | "看数据" = `month_summary` + `dashboard` + `list_months` + `perf_ranking` + `person_detail` |
| "导出" 拆成 4 个 | `export_salary` / `export_payroll_settle` / `export_recon_report` / `export_recon_result` / `export_recon_diff` |
| 店铺主档一件事 6 个 | `store_search` / `merge_pair` / `skip_pair` / `apply_all` / `split_entity` / `ai_run` |

**后果**：模型要在 49 个里选，选错率高；用户看到一堆看不懂的工具名。

---

## 二、设计原则

1. **一个工具 = 一个用户会问的问题 / 会派的一件事**（不是一次 API 调用）
2. **工具名用用户语言**（`visit_upload` 而不是 `visit_upload_recon`）
3. **同域不同视角用 `view` 枚举**（一次调用尽量答完整，减少往返）
4. **读写分离**：只读工具与写工具不混在一个工具里（注解与闸门依赖它）
5. **描述必须写"什么时候用我"**（互斥指引）——这是调用成功率的最大杠杆
6. **数量目标 ≈ 15**，但不追求"最少"：过度合并会造出"上帝工具"，模型照样选错

---

## 三、目标工具集（16 个）

### 员工侧（3 个，白名单）

| # | 工具 | 参数 | 说明 |
|---|---|---|---|
| 1 | `visit_whoami` | — | 我是谁：账号/角色/绑定人员/权限/可用能力 |
| 2 | `visit_my_perf` | `month?`, `view=month\|daily` | 我的绩效（月汇总 / 逐日明细） |
| 3 | `visit_my_pay` | `month?` | 我的找平与发放（找平进度 + 发放台账 + 回收轨迹） |

> 员工只能看本人：**无 `person` 参数**，服务端按 token 绑定人员过滤。

### 管理员侧（13 个）

| # | 工具 | 参数 | 合并掉的旧工具 |
|---|---|---|---|
| 4 | `visit_upload` | `filename`, `content_base64?`/`path?`, `kind?`, `dry_run?` | upload_file + upload_recon + finalize_file |
| 5 | `visit_overview` | `month?`, `view=summary\|months\|ranking` | month_summary + dashboard + list_months + perf_ranking |
| 6 | `visit_person` | `person` 或 `name`, `month?` | month_salary + person_detail |
| 7 | `visit_payroll` | `month`, `view=rows\|adjusts\|trace` | payroll_rows + settlement_trace + payroll_update |
| 8 | `visit_payroll_export` | `month`, `seq=1\|2`, `dry_run?` | export_salary + export_payroll_settle + payroll_generate + payroll_mark_paid |
| 9 | `visit_recon` | `month` 或 `task_id`, `view=status\|diff\|interpret` | recon_status + recon_diff + recon_interpret + recon_adjust + recon_adjust_state + recon_settlement |
| 10 | `visit_recon_export` | `month` 或 `task_id`, `kind=report\|detail\|diff` | export_recon_report + export_recon_result + export_recon_diff |
| 11 | `visit_files` | `view=list\|layout\|report\|tasks`, `import_id?` | file_list + file_layout + file_report + list_tasks |
| 12 | `visit_rebuild` | `month`, `action=preview\|run` | rebuild_preview + rebuild_month |
| 13 | `visit_staff` | `view=list` / `action=set_status`, `username?`, `status?` | staff_list + staff_set_status |
| 14 | `visit_config` | `view=get` / `action=set`, `per_point?`, `bonus_group?`, `bonus_amount?`, `staff_visible_from?`, `confirm_text?` | config_get + config_set + set_per_point |
| 15 | `visit_store` | `view=search\|pairs` / `action=merge\|split\|apply\|skip`, 相应参数 | store_search + merge_pair + skip_pair + apply_all + split_entity + store_ai_run |
| 16 | `visit_verify` | — | verify_integrity |

**移除**：`ping`（诊断用，非业务）、`product_doc`（改为工具描述内的指引，或保留为可选）。

### 权限矩阵

| 工具 | 员工 | 管理员 |
|---|---|---|
| `visit_whoami` / `visit_my_perf` / `visit_my_pay` | ✅ | ✅ |
| 其余 13 个 | ❌ `FORBIDDEN_TOOL` | ✅ |

> 保持**默认拒绝**：新增工具默认员工不可用（安全的失败方向）。

---

## 四、关键设计细节

### 4.1 `view` 枚举的写法（决定成败）

每个 `view` 值必须在描述里写清"**回答什么问题**"，例如：

```
visit_overview(view):
  summary  → 这个月多少店/多少点/多少钱/有没有对账（默认）
  months   → 有哪些月份有数据、各月规模（用于"有哪些月份"）
  ranking  → 谁多谁少（用于"排行""谁最高"）
```

**互斥指引**（必须写）：
- 看**全公司某月** → `visit_overview`
- 看**某个人** → `visit_person`
- 看**我自己的** → `visit_my_perf`（员工）／`visit_person`（管理员）

### 4.2 读写分离（注解正确）

| 工具 | 注解 |
|---|---|
| `visit_upload` / `visit_payroll_export` / `visit_rebuild(run)` / `visit_staff(set_status)` / `visit_config(set)` / `visit_store(merge/split/apply/skip)` | 写：`readOnlyHint=false` + 闸门（确认语/预演） |
| 其余 | 只读：`readOnlyHint=true` |

> 带 `action` 的写工具：**`action` 为写值时要求确认语**（沿用现有闸门机制）；
> 只读 `view` 不需要。

### 4.3 `dry_run` / `preview` 统一

- `visit_upload(dry_run=true)` → 只解析、不入表（报影响面）
- `visit_rebuild(action=preview)` → 只算差异（等同旧 `rebuild_preview`）
- `visit_payroll_export(dry_run=true)` → 只算金额，**不登记发放**（现有工具缺这个能力，用户曾要求过）

### 4.4 员工工具的"边界"表达

`visit_my_pay` 一次给全：找平进度（原始/已找平/剩余/状态）+ 发放台账（各期实发/点数/奖金/抵扣）+ 回收轨迹。
**不给**他人数据、**不给**公司级汇总——描述里明说"若用户问公司级数据，告知无权限"。

---

## 五、兼容与迁移

| 阶段 | 做法 |
|---|---|
| **兼容期** | 旧工具名**保留**，但描述开头标 `【已废弃，请用 visit_xxx(view=...)】`；行为不变 |
| **权限** | `STAFF_ALLOWED` 改为新员工工具集；旧员工工具名一并保留在名单里（避免兼容期员工断掉） |
| **裁剪** | `tools/list` 只暴露**新 16 个**（旧名仅在显式调用时可用）→ 列表立刻干净 |
| **移除** | 兼容期结束后（确认无人调用）删除旧工具 |

> 关键：**列表只给新的 16 个**，这样用户与模型立刻看到"场景化"的工具面；
> 旧名留着不暴露，保证任何已写死的调用不炸。

---

## 六、验收（重构后必须通过）

1. 每个新工具至少 1 条"能答对"的测试（用真实库数据断言关键字段）
2. `view`/`action` 的**非法值** → `BAD_PARAM` + 可选值提示
3. 员工越权调管理员工具 → `FORBIDDEN_TOOL`（无副作用）
4. `tools/list`：员工 3 个 / 管理员 16 个
5. **旧名仍可调用**（兼容期）
6. 关键数字与重构前一致（8月 12507/16787/4,895,750；9月 15070/19477/5,173,000；自洽 8/8+A9）
7. 审计：工具名记录正确（不是包装函数名）

---

## 七、工作量与顺序（建议）

| 步 | 内容 | 说明 |
|---|---|---|
| 1 | 实现 16 个新工具（薄封装，**内部调用现有能力函数，不重写业务**） | 主体工作 |
| 2 | `tools/list` 只暴露新集；旧名保留可调用 | 一行开关 |
| 3 | 权限矩阵 + 员工白名单更新 | 小 |
| 4 | 测试（验收 1–7） | 与实现同步写 |
| 5 | 描述打磨（互斥指引 + 错误码表） | 决定调用成功率 |
| 6 | 更新 `docs/MCP对接手册.md`（记录"场景粒度 vs 接口粒度"教训） | 收尾 |

---

## 八、待你确认的三点

1. **16 个的数量与切分**是否认可？（尤其：`visit_recon_export` 要不要并进 `visit_recon`）
2. **`ping` / `product_doc` 是否删除**？（我建议：删 `ping`；`product_doc` 并入描述或保留）
3. **兼容期长度**：旧名保留到什么时候（建议：下一个版本前保留，确认无人调用后删）
