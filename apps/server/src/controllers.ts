import { Controller, Get, Post, Body, Req, Res, Inject } from '@nestjs/common';
import type { Request, Response } from 'express';
import { z } from 'zod';
import { AuthService } from './auth';
import { Database } from './database';
import { BusinessError } from './errors';
import { PlansService } from './plans';

const loginSchema = z.object({ username: z.string().trim().min(1).max(128), password: z.string().min(1).max(512) }).strict();
const passwordSchema = z.object({ currentPassword: z.string().min(1).max(512), newPassword: z.string().min(6).max(128) }).strict();
function parse<T>(schema: z.ZodType<T>, input: unknown): T {
  const result = schema.safeParse(input);
  if (!result.success) throw new BusinessError('VALIDATION', '请检查必填内容及长度要求', 400);
  return result.data;
}
@Controller('auth')
export class AuthController {
  constructor(@Inject(AuthService) private readonly auth: AuthService,@Inject(PlansService)private readonly plans:PlansService) {}
  @Post('login') async login(@Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    if (req.get('Sec-Fetch-Site') === 'cross-site') throw new BusinessError('FORBIDDEN', '请从本站登录', 403);
    const input = parse(loginSchema, body); return this.plans.sessionLanding(await this.auth.login(input.username, input.password, req, res));
  }
  @Get('session') async session(@Req() req: Request) { return this.plans.sessionLanding(await this.auth.require(req, { allowPasswordChange: true })); }
  @Post('logout') logout(@Req() req: Request, @Res({ passthrough: true }) res: Response) { return this.auth.logout(req, res); }
  @Post('password') async password(@Body() body: unknown, @Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const input = parse(passwordSchema, body); return this.plans.sessionLanding(await this.auth.changePassword(req, res, input.currentPassword, input.newPassword),true);
  }
}
@Controller()
export class ReadController {
  constructor(@Inject(AuthService) private readonly auth: AuthService, @Inject(Database) private readonly db: Database) {}
  @Get('health/live') live() { return { ok: true }; }
  @Get('health/ready') async ready() { await this.db.query('SELECT 1'); return { ok: true }; }
  @Get('dashboard') async dashboard(@Req() req: Request) {
    await this.auth.require(req, { admin: true });
    const result = await this.db.query(`WITH latest AS (SELECT max(month) AS month FROM month_perf_records)
      SELECT (SELECT month FROM latest) AS month,
      (SELECT count(*)::int FROM users WHERE role IN ('staff','leader') AND is_active=true AND status='active') AS staff,
      count(*)::int AS people,coalesce(sum(records),0)::int AS records,coalesce(sum(points),0)::int AS points,coalesce(sum(salary),0)::bigint::text AS salary,
      (SELECT count(*)::int FROM bd_main_task WHERE status<>'disabled') AS batches,
      (SELECT count(*)::int FROM appeals WHERE status='pending') AS "openAppeals"
      FROM month_perf_records WHERE month=(SELECT month FROM latest)`);
    const row = result.rows[0]; return { ...row, salary: Number(row.salary) };
  }
  @Get('work/batches') async batches(@Req() req: Request) {
    const { user } = await this.auth.require(req);
    if(user.status !== 'active') throw new BusinessError('STAFF_NOT_ACTIVE','请假期间不能执行巡店作业',403);
    // Current membership and captain identity are checked in SQL. Role alone does not confer team access.
    const result = await this.db.query(`WITH visible_regions AS (
      SELECT r.* FROM bd_region_task r JOIN bd_main_task m ON m.id=r.main_task_id
      WHERE ($2::boolean OR (m.status<>'disabled' AND EXISTS (
        SELECT 1 FROM bd_team t JOIN bd_team_member tm ON tm.team_id=t.id
        WHERE t.id=r.team_id AND t.status='active' AND tm.person_code=$3 AND tm.end_date IS NULL AND tm.start_date <= (now() AT TIME ZONE 'Asia/Tokyo')::date
        AND ((r.leader_id=$1 AND tm.role='leader' AND NOT EXISTS (SELECT 1 FROM bd_team_member other WHERE other.team_id=t.id AND other.role='leader' AND other.end_date IS NULL AND other.person_code<>$3)) OR (tm.role='member' AND EXISTS (
          SELECT 1 FROM bd_grid_task g WHERE g.region_task_id=r.id AND g.assignee_id=$1
        )))
      )))
    ), visible_grids AS (
      SELECT g.* FROM bd_grid_task g JOIN visible_regions r ON r.id=g.region_task_id
      WHERE $2::boolean OR r.leader_id=$1 OR g.assignee_id=$1
    ) SELECT m.id,m.title,m.status,m.progress,
      count(DISTINCT r.id)::int AS regions,count(g.id)::int AS grids,
      coalesce(sum(g.total_stores),0)::int AS "totalStores",coalesce(sum(g.completed_stores),0)::int AS "completedStores"
      FROM bd_main_task m JOIN visible_regions r ON r.main_task_id=m.id LEFT JOIN visible_grids g ON g.region_task_id=r.id
      GROUP BY m.id ORDER BY m.created_at DESC,m.id DESC LIMIT 30`, [user.id, user.role === 'admin', user.personCode]);
    return { items: result.rows };
  }
}
