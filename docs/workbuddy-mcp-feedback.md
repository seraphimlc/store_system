# 巡店系统 MCP 服务改造需求（WorkBuddy 反馈）

> 来源：WorkBuddy 整合 MCP 后给出的意见（2026-09-25）。目标：让模型一次就能调对参数、错误可编程处理、危险语义前置声明。
> 注：文档前提"共 4 个工具"为早期快照，现已有 40 个工具；P0-4（note 引用不存在工具）已解决。

## 现状已确认事实
- 客户端配置 `~/.workbuddy/mcp.json`：streamableHttp → `http://127.0.0.1:8765/mcp` + Bearer Token。
- 上传自动识别（服务端实现）：巡店记录 / 对账明细，通过 `detected{kind, reason, month_inferred}` 暴露。
- 信封：成功 `{ok:true,data}`；失败 `{ok:false,error:{code,message,hint}}`（**已加 `retryable`**，码表见 `mcp_service/envelope.py`）。
- 同月重传=覆盖（旧任务 replaced_by、version+1）；字节相同文件=sha256 去重（DUPLICATE_FILE）。

## P0 — 必须修复
### P0-1 `visit_upload_file` 参数互斥约束
- `path` 与 `content_base64` 互斥且必须提供其一：两者都传 → `BAD_REQUEST`；都不传 → `BAD_REQUEST`。
- 描述首行写：`必须提供 path 或 content_base64 之一，不可同时提供，也不可都不提供。`

### P0-3 覆盖与去重语义声明（写进描述）
```
幂等与覆盖语义：
- 文件级去重：按内容 sha256。字节相同的文件重复上传返回 DUPLICATE_FILE，不重复入库。
- 同月覆盖：同一结算月的同类型数据重复上传会「覆盖」上一版本（旧任务 replaced、version 递增），不是追加。
- 不同月份互不影响。
```
- 返回中显式给出 `is_overwrite: true/false`。

### P0-5 错误码字典（写进描述，至少覆盖）
| 错误码 | 含义 | 可重试 |
|---|---|---|
| PARSE_FAILED | 文件无法解析 | 否，需修正文件 |
| DUPLICATE_FILE | 内容指纹已存在 | 否 |
| INTERNAL_WRITE | 服务端写入异常，**可能已部分生效** | **禁止自动重试** |
| TEMPLATE_SHEET_MISSING | 未找到巡店模板 sheet | 否 |
| MONTH_CLOSED / MONTH_SEALED | 目标月份已封账 | 否 |
| AUTH_FAILED / UNAUTHORIZED / FORBIDDEN_TOOL | 鉴权/权限不足 | 否 |
| BAD_REQUEST / BAD_PARAM / BAD_MONTH | 参数不合法 | 否 |

## P1 — 应当修复
### P1-6 tool annotations + title
- 每个工具加 MCP annotations：readOnlyHint / idempotentHint / destructiveHint / title。
- 参考：`visit_upload_file`（title=上传巡店/对账文件，readOnly=false，idempotent=false，destructive=true）；读工具 readOnly=true。

### P1-7 kind 统一
- 统一枚举：`daily_records`（巡店记录）| `recon`（对账数据）。
- `task.kind` 反映**实际识别结果**（与 `detected.kind` 一致），不再固定 `daily_records`。
- 描述里说明 `detected` 字段含义。

### P1-8/9 字段口径 + person 匹配规则
- `visit_month_salary` 描述补口径：points（总点数）/ p1·p2（1点/2点条数）/ per_point（单价 JPY）/ salary（应付）/ settle_amount（实际结算额，未对账为 0）/ diff_amount（=settle_amount−salary，未对账时**应为 0 而非 −salary**）。
- 明确 `person` 匹配规则：工号精确；姓名包含匹配；同名返回全部。

## P2 — 建议增强
### P2-10 `visit_list_tasks(month=None, kind=None, status=None)`
- 返回历史任务：id/month/kind/filename/status/version/is_previous/created_at/finished_at/replaced_previous_ids。
- kind 取值 `daily_records | recon`（映射：巡店=ImportFile 记录；对账=ReconTask）。

### P2-11 `visit_list_months()`
- 返回系统内有数据的月份列表（正式表/月绩效/对账任务三类各自计数）。

### P2-12 `visit_upload_file` 支持 `dry_run=true`
- 仅解析+识别+影响预估，不写入。返回：`{dry_run:true, detected, month, will_replace_task_ids, estimated_rows, estimated_persons}`。

## 验收
1. 参数互斥：都不传/都传 → BAD_REQUEST 并说明。
2. 失败全部 `{ok:false,error:{code,message,retryable,hint}}`，不抛裸异常。
3. 描述含错误码表，INTERNAL_WRITE 标注禁自动重试。
4. 描述声明「同月覆盖 + sha256 去重」。
5. note 中提到的工具名都真实存在。
6. 工具带 annotations。
7. `task.kind` 与 `detected.kind` 一致。
8. `visit_list_tasks` / `visit_list_months` 可用。
9. `dry_run:true` 零写入且返回影响预估。

## 约束
- 不破坏现有正确行为与返回字段（可新增字段，不可删既有字段）。
- 金额一律 JPY；月份统一 `YYYY-MM`。
- 向后兼容：失败信封从"抛异常"改为结构化返回属扩展。
