import {aiLoginCandidates} from './login-name-model';import type {ReportModel} from './report-model';
import { Inject, Injectable } from '@nestjs/common';
import { hash } from '@node-rs/argon2';
import { pinyin } from 'pinyin-pro';
import ExcelJS from 'exceljs';
import type { PoolClient, QueryResultRow } from 'pg';
import { Database } from './database';
import { BusinessError } from './errors';
import { passwordWork } from './password-work';

export const employeeStates = ['active', 'leave', 'disabled', 'resigned'] as const;
export type EmployeeState = typeof employeeStates[number];
export interface EmployeeInput {
  code: string; name: string; username: string; password: string; role: 'staff' | 'leader';
}
export interface EmployeeEdit {
  name: string; username: string; status: EmployeeState; language: '' | 'zh' | 'ja';
  role: 'staff' | 'leader'; password: string;
  expected: { username: string; name: string; status: EmployeeState; language: string; role: string };
}
interface EmployeeRow extends QueryResultRow {
  id: number; username: string; display_name: string; person_code: string | null;
  status: EmployeeState; role: 'staff' | 'leader'; lang: string; is_active: boolean; must_change_password: boolean;
}
export function cleanUsername(value: string) { return value.toLowerCase().replace(/[^a-z0-9-]/g, '').replace(/^-+|-+$/g, ''); }
export function nameCandidate(name: string, code: string) {
  const candidate = pinyin(name, { toneType: 'none', type: 'array' }).join('').toLowerCase().replace(/[^a-z0-9]/g, '');
  return candidate.length >= 3 ? candidate : 'emp' + (code.replace(/\D/g, '').slice(-6) || '0');
}
const order = `CASE status WHEN 'active' THEN 0 WHEN 'leave' THEN 1 WHEN 'disabled' THEN 2 WHEN 'resigned' THEN 3 ELSE 5 END,status,person_code NULLS LAST,id`;
export const employeeOrder = order;
function conflict(error: unknown): never {
  if ((error as { code?: string }).code === '23505') {
    const username = String((error as { constraint?: string }).constraint).includes('username');
    throw new BusinessError(username ? 'USERNAME_EXISTS' : 'CODE_EXISTS', username ? '该登录名已被占用，没有做任何修改' : '该编号已存在', 409);
  }
  throw error;
}

@Injectable()
export class EmployeeService {
  constructor(@Inject(Database) private readonly db: Database) {}
  private defaultPassword() { return process.env.NEXT_DEFAULT_STAFF_PASSWORD || 'demo123'; }
  async suggest(name: string, code: string, preferred = '', client?: PoolClient) {
    const base = cleanUsername(preferred) || nameCandidate(name, code);
    const sql = 'SELECT username FROM users WHERE username LIKE $1';
    const names = client ? await client.query<{ username: string }>(sql, [base + '%']) : await this.db.query<{ username: string }>(sql, [base + '%']);
    const taken = new Set(names.rows.map(row => row.username));
    if (!taken.has(base)) return base;
    const digits = code.replace(/\D/g, '');
    const candidate = base + (digits.slice(-4) || 'x');
    if (!taken.has(candidate)) return candidate;
    let index = 2;
    while (taken.has(base + index)) index++;
    return base + index;
  }
  async propose(name:string,code:string,model?:ReportModel){
    const candidates=await aiLoginCandidates([name],model),preferred=candidates.get(name.trim());
    return {username:await this.suggest(name,code,preferred),source:preferred?'model':'rule'};
  }
  async create(input: EmployeeInput,model?:ReportModel) {
    const code = input.code.normalize('NFKC').trim();
    if (!code || code.length > 32) throw new BusinessError('VALIDATION', '员工编号必填且不超过32位', 400);
    if((await this.db.query('SELECT code FROM persons WHERE code=$1 UNION ALL SELECT person_code FROM users WHERE person_code=$1',[code])).rowCount)throw new BusinessError('CODE_EXISTS','该编号已存在',409);
    const preferred=cleanUsername(input.username)||(await aiLoginCandidates([input.name],model)).get(input.name.trim())||'';
    const passwordHash = await passwordWork.run(() => hash(input.password || this.defaultPassword()));
    try {
      return await this.db.transaction(async client => {
        if ((await client.query('SELECT code FROM persons WHERE code=$1 UNION ALL SELECT person_code FROM users WHERE person_code=$1', [code])).rowCount) {
          throw new BusinessError('CODE_EXISTS', '该编号已存在', 409);
        }
        const username = await this.suggest(input.name, code, preferred, client);
        await client.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$2,now())', [code, input.name.trim()]);
        const row = (await client.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at)
          VALUES($1,$2,$3,$4,$5,'active',true,true,'',now()) RETURNING id,username,person_code AS "personCode"`,
          [username, passwordHash, input.name.trim(), code, input.role])).rows[0];
        return row;
      });
    } catch (error) { conflict(error); }
  }
  async edit(id: number, input: EmployeeEdit) {
    const username = cleanUsername(input.username);
    if (!username) throw new BusinessError('VALIDATION', '登录名只能用字母、数字和连字符', 400);
    const passwordHash = input.password ? await passwordWork.run(() => hash(input.password)) : null;
    try {
      return await this.db.transaction(async client => {
        const target = (await client.query<EmployeeRow>(`SELECT * FROM users WHERE id=$1 AND role IN ('staff','leader') AND status<>'deleted' FOR UPDATE`, [id])).rows[0];
        if (!target) throw new BusinessError('NOT_FOUND', '员工不存在', 404);
        const expected = input.expected;
        if (target.username !== expected.username || target.display_name !== expected.name || target.status !== expected.status || (target.lang || '') !== expected.language || target.role !== expected.role) {
          throw new BusinessError('CONFLICT', '员工资料已更新，请刷新后重试', 409);
        }
        if ((await client.query('SELECT id FROM users WHERE username=$1 AND id<>$2', [username, id])).rowCount) {
          throw new BusinessError('USERNAME_EXISTS', '该登录名已被占用，没有做任何修改', 409);
        }
        await client.query(`UPDATE users SET display_name=$1,username=$2,status=$3,is_active=$4,lang=$5,role=$6,
          password_hash=coalesce($7,password_hash),must_change_password=CASE WHEN $7::text IS NULL THEN must_change_password ELSE true END WHERE id=$8`,
          [input.name.trim(), username, input.status, ['active', 'leave'].includes(input.status), input.language, input.role, passwordHash, id]);
        if (target.person_code) await client.query('UPDATE persons SET display_name=$1 WHERE code=$2', [input.name.trim(), target.person_code]);
        return { id, username, passwordReset: !!passwordHash };
      });
    } catch (error) { conflict(error); }
  }
  async remove(id: number, expectedStatus: EmployeeState) {
    return this.db.transaction(async client => {
      const target = (await client.query("SELECT status FROM users WHERE id=$1 AND role IN ('staff','leader') AND status<>'deleted' FOR UPDATE", [id])).rows[0];
      if (!target) throw new BusinessError('NOT_FOUND', '员工不存在或已删除', 404);
      if (target.status !== expectedStatus) throw new BusinessError('CONFLICT', '员工状态已更新，请刷新后重试', 409);
      await client.query("UPDATE users SET status='deleted',is_active=false WHERE id=$1", [id]);
      return { id, deleted: true };
    });
  }
  async reset(id: number | null, password: string) {
    const passwordHash = await passwordWork.run(() => hash(password));
    const result = await this.db.query(`UPDATE users SET password_hash=$1,must_change_password=true
      WHERE role IN ('staff','leader') AND status<>'deleted' AND ($2::int IS NULL OR id=$2) RETURNING id`, [passwordHash, id]);
    if (id !== null && !result.rowCount) throw new BusinessError('NOT_FOUND', '员工不存在', 404);
    return { count: result.rowCount };
  }
  async export(onlyActive: boolean, origin: string) {
    const rows = (await this.db.query<EmployeeRow>(`SELECT id,username,display_name,person_code,status,role,lang,is_active,must_change_password
      FROM users WHERE role IN ('staff','leader') AND status<>'deleted' ${onlyActive ? "AND status='active' AND is_active=true" : ''} ORDER BY ${order}`)).rows;
    const workbook = new ExcelJS.Workbook();
    const sheet = workbook.addWorksheet('员工登录名');
    sheet.addRow(['登录名','姓名','员工编号','在职状态','账号启用','首次登录需改密','初始口令','界面语言','登录地址']);
    for (const row of rows) sheet.addRow([row.username,row.display_name,row.person_code || '',row.status,row.is_active ? '是' : '否',row.must_change_password ? '是' : '',row.must_change_password ? this.defaultPassword() : '',row.lang || '自动',origin + '/login']);
    sheet.columns.forEach((column, index) => { column.width = [18,18,24,12,12,18,18,12,38][index]; });
    sheet.getRow(1).font = { bold: true };
    const instructions = workbook.addWorksheet('使用说明');
    instructions.addRows([['项目','说明'],['登录地址',origin + '/login'],['初始口令',this.defaultPassword() + '（仅用于首次登录）'],['首次登录','系统强制要求改为自己的密码'],['自定义重置口令','导出的初始口令为系统默认值；管理员自定义的口令请单独交付'],['忘记密码','请联系管理员重置']]);
    return Buffer.from(await workbook.xlsx.writeBuffer());
  }
}
