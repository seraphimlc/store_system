import { Inject, Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';
import type { McpActor } from './mcp';
import { McpReadError } from './mcp-read';
import { EmployeeService, EmployeeState, employeeStates } from './employee-service';
import { ConfigurationService } from './configuration';
import { BusinessError } from './errors';

export function isMcpManagementWrite(tool: string, args: Record<string, unknown>) {
  return tool === 'visit_staff' && args.action === 'set_status' || tool === 'visit_config' && args.action === 'set';
}
const annotations = { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: false };
const nullableText = { type: ['string', 'null'] };
export const mcpManagementWriteDefinitions = {
  visit_staff: { description: '仅管理员：默认 view=list 查询账号；action=set_status 修改员工/队长状态，需 read,write Token、username、status 和确认语“确认改状态 用户名 状态”。状态限 active/leave/disabled/resigned，不复活删除账号。可传 expected_status 防止覆盖已变化状态。查询 last_login 实为最近 MCP 使用时间。',
    inputSchema: { type: 'object' as const, properties: { view: { type: ['string', 'null'], enum: ['list', null] }, action: { type: ['string', 'null'], enum: ['set_status', null] }, username: nullableText, status: nullableText, confirm_text: nullableText, expected_status: nullableText }, additionalProperties: false }, annotations },
  visit_config: { description: '仅管理员：默认 view=get 查询。action=set 保存全局业务配置，需 read,write Token 和确认语“确认修改配置”。可选 per_point、bonus_group、bonus_amount、staff_visible_from，省略保留原值；不重算历史工资、不修改实发。可传 expected={id,version} 防止覆盖已变化配置；版本取自 include_version=true 配置查询。',
    inputSchema: { type: 'object' as const, properties: { view: { type: ['string', 'null'], enum: ['get', null] }, include_version: { type: 'boolean', default: false }, action: { type: ['string', 'null'], enum: ['set', null] }, per_point: { type: ['integer', 'null'], minimum: 1, maximum: 10000 }, bonus_group: { type: ['integer', 'null'], minimum: 1, maximum: 1000 }, bonus_amount: { type: ['integer', 'null'], minimum: 0, maximum: 1000000 }, staff_visible_from: nullableText, confirm_text: nullableText,
      expected: { type: ['object', 'null'], properties: { id: { type: ['integer', 'null'] }, version: nullableText }, required: ['id', 'version'], additionalProperties: false } }, additionalProperties: false }, annotations },
};
function confirm(value: unknown, expected: string) {
  if (typeof value !== 'string' || value.trim() !== expected) throw new McpReadError('CONFIRM_REQUIRED', '确认语不匹配', `请复述确认语：${expected}`);
}
function number(value: unknown, fallback: number, minimum: number, maximum: number, label: string) {
  if (value == null) return fallback;
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum || value > maximum) throw new McpReadError('BAD_PARAM', `${label} 必须是 ${minimum}~${maximum} 的整数`);
  return value;
}

@Injectable()
export class McpManagementWriteService {
  constructor(@Inject(EmployeeService) private readonly employees: EmployeeService, @Inject(ConfigurationService) private readonly configuration: ConfigurationService) {}
  async run(client: PoolClient, actor: McpActor, tool: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
    if (actor.role !== 'admin' || !actor.scopes.includes('write')) throw new McpReadError('FORBIDDEN_TOOL', '该操作仅限管理员且需要写权限 Token', '请使用 read,write 凭据');
    if (!isMcpManagementWrite(tool, args)) throw new McpReadError('BAD_PARAM', '写入操作无效');
    const allowed = tool === 'visit_staff' ? ['view', 'action', 'username', 'status', 'confirm_text', 'expected_status'] : ['view', 'action', 'per_point', 'bonus_group', 'bonus_amount', 'staff_visible_from', 'confirm_text', 'expected'];
    if (Object.keys(args).some(key => !allowed.includes(key)) || args.view != null && args.view !== (tool === 'visit_staff' ? 'list' : 'get')) throw new McpReadError('BAD_PARAM', '写入参数或视图无效');
    try {
      return tool === 'visit_staff' ? await this.status(client, args) : await this.config(client, actor, args);
    } catch (error) {
      if (error instanceof BusinessError) throw new McpReadError(error.code === 'VALIDATION' ? 'BAD_PARAM' : error.code, error.message, error.getStatus() === 409 ? '先查询最新状态，再确认并重试' : '');
      throw error;
    }
  }
  private async status(client: PoolClient, args: Record<string, unknown>) {
    if (typeof args.username !== 'string' || !args.username.trim() || typeof args.status !== 'string' || !employeeStates.includes(args.status as EmployeeState)) throw new McpReadError('BAD_PARAM', '需要员工用户名及有效状态');
    const username = args.username.trim();
    confirm(args.confirm_text, `确认改状态 ${username} ${args.status}`);
    const target = (await client.query('SELECT id,username,display_name,status FROM users WHERE username=$1', [username])).rows[0];
    if (!target) throw new McpReadError('NOT_FOUND', '员工不存在');
    const expected = args.expected_status ?? target.status;
    if (!employeeStates.includes(expected as EmployeeState)) {
      if (target.status === 'deleted') throw new McpReadError('NOT_FOUND', '员工不存在');
      throw new McpReadError('BAD_PARAM', 'expected_status 无效');
    }
    const result = await this.employees.setStatus(client, target.id, args.status as EmployeeState, expected as EmployeeState);
    const labels = { active: '在岗', leave: '请假', disabled: '停用', resigned: '离职' };
    return { user_id: target.id, username: target.username, display_name: target.display_name, new_status: result.status, status_label: labels[result.status], is_active: result.isActive,
      hint: '状态已更新（数据永不删除）；停用/离职后该账号不可登录' };
  }
  private async config(client: PoolClient, actor: McpActor, args: Record<string, unknown>) {
    confirm(args.confirm_text, '确认修改配置');
    const current = (await client.query('SELECT id,updated_at::text AS version,per_point,bonus_group,bonus_amount,staff_visible_from FROM sys_configs ORDER BY id DESC LIMIT 1')).rows[0];
    const perPoint = number(args.per_point, current?.per_point ?? 250, 1, 10000, 'per_point');
    const bonusGroup = number(args.bonus_group, current?.bonus_group ?? 68, 1, 1000, 'bonus_group');
    const bonusAmount = number(args.bonus_amount, current?.bonus_amount ?? 3000, 0, 1000000, 'bonus_amount');
    const visible = args.staff_visible_from == null ? current?.staff_visible_from ?? '' : args.staff_visible_from;
    if (typeof visible !== 'string' || visible.trim() && !/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(visible.trim())) throw new McpReadError('BAD_PARAM', 'staff_visible_from 必须是 YYYY-MM 或空文本');
    const expected = args.expected ?? { id: current?.id ?? null, version: current?.version ?? null };
    if (!expected || typeof expected !== 'object' || Array.isArray(expected) || Object.keys(expected).some(key => !['id', 'version'].includes(key))) throw new McpReadError('BAD_PARAM', 'expected 格式无效');
    await this.configuration.saveInTransaction(client, { perPoint, bonusGroup, bonusAmount, staffVisibleFrom: visible.trim(), expected: expected as { id: number | null; version: string | null } }, actor.uid);
    return { per_point: perPoint, bonus_group: bonusGroup, bonus_amount: bonusAmount, staff_visible_from: visible.trim(), currency: 'JPY',
      hint: '该配置为全局单值（最新一条生效），会影响所有月份工资口径；如需按月不同请用 env 的 BONUS_*_SCHEDULE' };
  }
}
