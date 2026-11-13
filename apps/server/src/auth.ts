import {knownDefaultPassword,rejectDefaultPassword} from './password-policy';
import { Inject, Injectable } from '@nestjs/common';
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { verify, hash } from '@node-rs/argon2';
import type { Request, Response } from 'express';
import type { CurrentUser, Session } from '../../../packages/contracts/src';
import { CONFIG, AppConfig } from './config';
import { Database } from './database';
import { passwordWork } from './password-work';
import { BusinessError } from './errors';

interface UserRow { id: number; username: string; password_hash: string; display_name: string; person_code: string | null;
  role: string; status: string; is_active: boolean; must_change_password: boolean; lang: string | null }
interface Claims { uid: number; csrf: string; expires: number; fingerprint: string; sid: string }
const COOKIE = 'store_session';
const TTL = 12 * 3600;
export function activeUser(row: UserRow | undefined): row is UserRow {
  return !!row && row.is_active && ['active','leave'].includes(row.status) && ['admin', 'leader', 'staff'].includes(row.role);
}
function publicUser(row: UserRow): CurrentUser {
  return { id: row.id, username: row.username, displayName: row.display_name || row.username,
    personCode: row.person_code, role: row.role as CurrentUser['role'], status: row.status as CurrentUser['status'], language: row.lang === 'ja' ? 'ja' : 'zh', languagePreference: row.lang === 'ja' || row.lang === 'zh' ? row.lang : '', mustChangePassword: row.must_change_password };
}
function fingerprint(row: UserRow) { return createHash('sha256').update(row.password_hash).digest('base64url'); }
function equal(a: string, b: string) { const x = Buffer.from(a); const y = Buffer.from(b); return x.length === y.length && timingSafeEqual(x, y); }

@Injectable()
export class AuthService {
  // Bounded process-local login throttle; no persistent database/schema changes.
  private readonly failures = new Map<string, { count: number; until: number }>();
  constructor(@Inject(Database) private readonly db: Database, @Inject(CONFIG) private readonly config: AppConfig) {}
  private sign(value: string) { return createHmac('sha256', this.config.sessionSecret).update(value).digest('base64url'); }
  private async writeSession(row: UserRow, response: Response): Promise<Session> {
    const claims: Claims = { uid: row.id, csrf: randomBytes(24).toString('base64url'), expires: Math.floor(Date.now() / 1000) + TTL, fingerprint: fingerprint(row), sid: randomBytes(32).toString('base64url') };
    await this.db.query("INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES($1,$2,to_timestamp($3))", [createHash('sha256').update(claims.sid).digest('hex'),row.id,claims.expires]);
    const value = Buffer.from(JSON.stringify(claims)).toString('base64url');
    response.cookie(COOKIE, `${value}.${this.sign(value)}`, { httpOnly: true, sameSite: 'lax', secure: this.config.cookieSecure, path: '/', maxAge: TTL * 1000 });
    return { user: publicUser(row), csrfToken: claims.csrf };
  }
  private claims(request: Request): Claims {
    const cookie = request.headers.cookie?.split(';').map(x => x.trim()).find(x => x.startsWith(COOKIE + '='))?.slice(COOKIE.length + 1) ?? '';
    if (cookie.length > 2048) throw new BusinessError('UNAUTHENTICATED', '请先登录', 401);
    const [payload, signature, extra] = cookie.split('.');
    if (!payload || !signature || extra || !equal(this.sign(payload), signature)) throw new BusinessError('UNAUTHENTICATED', '请先登录', 401);
    try {
      const value = JSON.parse(Buffer.from(payload, 'base64url').toString()) as Claims;
      if (!Number.isSafeInteger(value.uid) || typeof value.csrf !== 'string' || !Number.isFinite(value.expires) || value.expires <= Date.now() / 1000 || typeof value.fingerprint !== 'string' || typeof value.sid !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(value.sid)) throw new Error();
      return value;
    } catch { throw new BusinessError('UNAUTHENTICATED', '登录已失效，请重新登录', 401); }
  }
  private async authenticated(request: Request) {
    const claims = this.claims(request);
    const row = (await this.db.query<UserRow>(`SELECT u.id,u.username,u.password_hash,u.display_name,u.person_code,u.role,u.status,u.is_active,u.must_change_password,u.lang FROM users u JOIN auth_sessions s ON s.user_id=u.id WHERE u.id=$1 AND s.token_hash=$2 AND s.revoked_at IS NULL AND s.expires_at>now()`, [claims.uid,createHash('sha256').update(claims.sid).digest('hex')])).rows[0];
    if (!activeUser(row) || !equal(claims.fingerprint, fingerprint(row))) throw new BusinessError('UNAUTHENTICATED', '账号状态已变化，请重新登录', 401);
    return { row, claims };
  }
  async require(request: Request, options: { admin?: boolean; allowPasswordChange?: boolean; write?: boolean } = {}) {
    const { row, claims } = await this.authenticated(request);
    if (options.write && !equal(request.get('X-CSRF-Token') ?? '', claims.csrf)) throw new BusinessError('CSRF_INVALID', '提交验证已失效，请刷新重试', 403);
    if (!options.allowPasswordChange && row.must_change_password) throw new BusinessError('PASSWORD_CHANGE_REQUIRED', '请先修改初始密码', 403);
    if (options.admin && row.role !== 'admin') throw new BusinessError('FORBIDDEN', '没有管理端访问权限', 403);
    return { user: publicUser(row), csrfToken: claims.csrf };
  }
  async login(username: string, password: string, request: Request, response: Response) {
    const throttleKey = request.ip ?? 'unknown';
    const prior = this.failures.get(throttleKey);
    if (prior && prior.until > Date.now() && prior.count >= 8) throw new BusinessError('TOO_MANY_ATTEMPTS', '尝试过于频繁，请稍后重试', 429);
    const row = (await this.db.query<UserRow>('SELECT id,username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang FROM users WHERE username=$1', [username])).rows[0];
    const valid = row && !knownDefaultPassword(password) ? await passwordWork.run(() => verify(row.password_hash, password).catch(() => false)) : false;
    if (!valid || !activeUser(row)) {
      if (this.failures.size >= 1000) this.failures.delete(this.failures.keys().next().value!);
      this.failures.set(throttleKey, { count: prior && prior.until > Date.now() ? prior.count + 1 : 1, until: Date.now() + 15 * 60000 });
      throw new BusinessError('INVALID_CREDENTIALS', '账号或密码错误，或账号当前不可用', 401);
    }
    this.failures.delete(throttleKey);
    return this.writeSession(row, response);
  }
  async logout(request: Request, response: Response) {
    await this.require(request, { allowPasswordChange: true, write: true });
    await this.db.query('UPDATE auth_sessions SET revoked_at=now() WHERE token_hash=$1 AND revoked_at IS NULL',[createHash('sha256').update(this.claims(request).sid).digest('hex')]);
    response.clearCookie(COOKIE, { httpOnly: true, sameSite: 'lax', secure: this.config.cookieSecure, path: '/' });
    return { ok: true };
  }
  async changePassword(request: Request, response: Response, current: string, next: string) {
    const { row, claims } = await this.authenticated(request);
    if (!equal(request.get('X-CSRF-Token') ?? '', claims.csrf)) throw new BusinessError('CSRF_INVALID', '提交验证已失效，请刷新重试', 403);
    if (!await passwordWork.run(() => verify(row.password_hash, current).catch(() => false))) throw new BusinessError('PASSWORD_INCORRECT', '当前密码不正确', 400);
    rejectDefaultPassword(next);
    const nextHash = await passwordWork.run(() => hash(next));
    const updated = await this.db.transaction(async client=>{
      const result = await client.query<UserRow>(`UPDATE users SET password_hash=$1,must_change_password=false WHERE id=$2 AND password_hash=$3 AND is_active=true AND status IN ('active','leave') RETURNING *`, [nextHash, row.id, row.password_hash]);
      if (!result.rows[0]) throw new BusinessError('CONFLICT', '账号已变化，请重新登录后重试', 409);
      await client.query('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=$1 AND revoked_at IS NULL',[row.id]);
      await client.query("UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE user_id=$1",[row.id]);
      await client.query("UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE user_id=$1",[row.id]);
      await client.query('DELETE FROM oauth_codes WHERE user_id=$1 AND used_at IS NULL',[row.id]);
      return result.rows[0];
    });
    return this.writeSession(updated, response);
  }
}
