# 站内消息 + 任务进展「确认 / 调整」（2026-10-03 交付）

> 用户原话（要点）：
> 「员工每天任务上报后，队长可以做调整和确认。对于调整和确认结果，要以消息的形式通知给员工……
> 还要规划一个消息通知功能。管理员可以发消息给员工。队长也可以发消息给员工。这里的员工指一个，
> 多个，全体等。消息模块就是类似其它网站或者系统的消息模块。」

## 0. 实施记录（**已实现**，本节是权威口径）

| # | 决定 / 落地 | 说明 |
|---|---|---|
| 1 | **进展审核**：员工先上报 → 队长**确认**或**调整** | `bd_task_progress` 加 6 列：`reported_pct`（员工原值）/`reported_by`/`review_status`/`reviewed_by`/`reviewed_at`/`review_note`。**`pct` = 当前生效值**，员工原值**永不覆盖**（用于"队员报 80%（已调成 70%）"对比展示） |
| 2 | 三个审核状态 | `pending`（等确认）/ `confirmed`（认可，值与上报一致）/ `adjusted`（队长改了值）。**"待确认"的展示口径 = `reported_pct is not null 且 review_status='pending'`**（历史/直接填报的行不会误显示待确认） |
| 3 | 队长的两个动作 | **确认**：`POST /my/tasks/confirm`（认可原值，一键）；**调整**：沿用 `POST /my/tasks/progress`（滑动条提交，值与上报不同 → `adjusted`） |
| 4 | 队长专用「**待确认**」tab | `/my/tasks?tab=pending`（+计数 +顶部 `pending-hint`）—— 否则待确认的活会藏在"进行中"里，队长根本找不到 |
| 5 | **确认/调整都要发消息给员工** | 系统自动发（`scope=task_review`，带 `url=/tasks/{id}` 可直达）。文案：确认 →「你上报的 80% 已被小川逸确认」；调整 →「你上报的 90%，被小川逸调整为 60%」 |
| 6 | 谁算"发件人" | **做这件事的人**（队长/管理员），不是无名的 system —— 员工要知道是谁改的。`sender_kind=leader/admin`，`scope=task_review` 用于界面分类 |
| 7 | **没有员工上报时队长直接填 → 不发"调整"消息** | 没有可比的原值，发了反而困惑 |
| 8 | 消息数据模型 | `bd_message`（一条消息一行：发件人/标题/正文/跳转 url/scope/ref）+ `bd_message_recipient`（**每个收件人一行**，`read_at` 按人独立）→ 一个/多个/全体都是同一条消息 |
| 9 | **收件人范围（数据隔离唯一入口）** | `bd_msg.recipients_for()`：管理员 → 指定的人 / **全体**（在岗+请假）；队长 → **只能本队现役队员**（"全体"= 我的队全体），**越界直接拒绝且不发**；员工 → 不能发 |
| 10 | 消息页 `/messages` | 三个 tab：**收件箱**（未读标黄、只看未读筛选、全部标已读、单条标已读）/ **我发出的**（收件人数 + 已读人数）/ **发消息**（选人 checkbox + 「全体」单选） |
| 11 | 未读红点 | 中间件注入 `request.state.unread_messages`（一次 COUNT；表没建也不 500）→ 员工端底栏「消息」tab 红点 + 管理端顶栏「消息（N）」 |
| 12 | 点开消息 | `GET /messages/{id}/go` → **标记已读**并 302 到 `url`（如任务详情）；**不是我的消息 → 回列表且不改已读**（越权保护） |
| 13 | ⚠️ 管理员账号可能**没有 `person_code`** | 消息模块的登录判据**不能**用 `person_code`（否则管理员连发件箱都进不去）；管理员特判放行，收件箱为空而已 |
| 14 | 边界不变 | 消息/审核**只写 `bd_message`/`bd_message_recipient`/`bd_task_progress`/`bd_log`**（全是作业域表）；守门测试 `test_message_send_only_writes_bd_tables` |

## 1. 数据模型

```
bd_message            id / sender_kind(system|admin|leader) / sender / sender_name
                      / title / body / url / scope(manual|task_review|announce)
                      / ref_type / ref_id / created_at
bd_message_recipient  id / message_id(FK, CASCADE) / person_code / read_at / created_at
                      UNIQUE(message_id, person_code)
```

`bd_task_progress` 追加：`reported_pct`(nullable) / `reported_by` / `review_status`
/ `reviewed_by` / `reviewed_at` / `review_note`。

迁移 `b4c5d6e7f8a9`（down_revision `a3b4c5d6e7f8`）。

## 2. 关键实现点

- **`save_progress(..., actor_user=, confirm=)` 一个入口**处理"员工上报"与"队长确认/调整"：
  员工（本人是担当且不是该队队长）→ 写 `reported_*` + `pending`；
  队长/管理员 → 按"是否与上报一致"落 `confirmed`/`adjusted` 并发消息。
- **`confirm=True`** 表示"认可"：直接用 `reported_pct` 作为生效值（不用前端再传一遍值）。
- **消息通知只在该有原值时发**（`reported_pct is not None`）。
- 消息与审核都写 `bd_log`（`domain=message` / `field=进展确认`），可追溯"谁什么时候改的"。

## 3. 测试（`tests_web/test_team_task.py`）

员工上报 → pending；队长确认 → `confirmed` + 消息（收件人只有担当、`read_at` 空）；
队长调整 → `pct=70`/`reported_pct=80` 都保留 + 消息里 80→70；
队长直接填不通知；管理员发全体 + 员工已读；队长越界被拒且不发；
员工不能发；`/go` 标已读并跳转；别人不能读我的消息；发消息只写 `bd_` 表；待确认 tab。
