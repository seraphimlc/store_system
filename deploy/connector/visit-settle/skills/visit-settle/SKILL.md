# 巡店结算

调用巡店结算系统：上传巡店/对账文件、查询绩效与薪资、核对找平与发放、导出发薪表。

**身份与权限（自动，无需配置 Token）**：连接时客户端自动完成 OAuth 授权（浏览器登录巡店系统账号）。
- **员工**：只能访问**本人**数据（`visit_my_perf` 我的绩效 / `visit_my_pay` 我的找平与发放 /
  `visit_my_tasks` 我的任务 / `visit_self_report` 每日自报 / `visit_whoami` 我是谁 等 6 个）；
  调用管理员工具会被拒绝（`FORBIDDEN_TOOL`）。
- **队长**：员工那 6 个 + 本队作业 4 个（`visit_team_tasks` 本队任务 / `visit_task_assign` 派工 /
  `visit_task_confirm` 确认进展 / `visit_task_transfer` **转给别的队**），共 10 个；只能看/管**本队**。
- **管理员**：可用**全部 25 个**（员工 6 + 队长 4 + 管理员 15）。
- 员工状态变更（请假/停用/离职）会**立即使其凭据失效**。

## 口径

- 月份一律 `YYYY-MM`（如 `2026-09`）。不要写 `2026/9`、`9月`、全角数字。
- 「正式表」= 已判定并入表的结算记录，是工资与对账的基准。
- 点数：管理端口径为 1 点/2 点两类；2 点指该店当月有投放。
- 金额规则：每点单价按月可配；奖金按「每满门槛点」计（门槛与奖额均按月可配）。
- 找平金额正负含义：**负 = 扣款，正 = 补款**。
- **金额单位一律为日元（円）**，工具返回 `currency: JPY`；不要表述为"元/人民币"。
- 薪资一律读已物化数据（工具返回值即已结算口径），**不要自己按单价重算**。

## 工具速查（16 个，场景化）

| 工具 | 用途 |
|---|---|
| `visit_whoami` | 我是谁（员工/管理员均可用） |
| `visit_my_perf` | 我的绩效：view=month 月汇总 / daily 逐日 |
| `visit_my_pay` | 我的找平与发放（找平进度+台账+回收轨迹） |
| `visit_upload` | 统一上传：自动识别巡店/对账文件；dry_run=true 先预估不写入 |
| `visit_overview` | 公司级总览：view=summary/months/ranking |
| `visit_person` | 某个人：月汇总+逐日明细（person 或 name） |
| `visit_payroll` | 薪资找平：view=rows 两期 / adjusts 找平状态 / trace 双向轨迹 |
| `visit_payroll_export` | 导出发薪表（seq=1/2）；dry_run=true 只算金额**不登记发放** |
| `visit_recon` | 对账：view=status 任务 / diff 差异 / interpret 已有 AI 解读 |
| `visit_recon_export` | 导出对账文件：kind=report/detail/diff |
| `visit_files` | 文件与任务：view=list/layout/report/tasks |
| `visit_rebuild` | 月度重算：action=preview 预演 / run 正式重算（需确认语） |
| `visit_staff` | 员工账号：view=list / action=set_status（写） |
| `visit_config` | 系统配置：view=get / action=set（写） |
| `visit_store` | 店铺主档：view=search/pairs / action=merge/split/apply/skip（写） |
| `visit_verify` | 数据自洽检查（只读，8 项互证） |

## 使用规则

1. **先查后写**：任何写操作前，先用只读工具确认当前状态。
2. **写操作需谨慎**：上传/重算/导出发薪表/改配置/店铺合并等会改数据；执行前先说明影响，
   重算/改配置/店铺合并等需按工具要求给出确认语（confirm_text）。
3. 不要自行推算或改写数字；只呈现工具返回的值。
4. **导出发薪表 = 登记"已发"事实**（`visit_payroll_export` 会把该期标记为已发放并自动抵扣找平）。
   若只是想看/核对金额，**务必先 `dry_run=true`**（只算金额、不登记发放），确认后再正式导出；
   只看两期明细用只读的 `visit_payroll(view='rows')`。
5. **已发薪月份的口径不会被改写**（历史工资已发）；店铺合并等自动化会跳过已发月份。
6. 上传文件时**不要自己判断类型**：`visit_upload` 会自动识别巡店记录/对账明细；
   识别不出时会返回选项，此时**问用户**再带 `kind` 重传。
7. 月份没人问就别猜，先问清楚是哪个月。
