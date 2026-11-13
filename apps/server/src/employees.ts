import { Body, Controller, Get, Inject, Param, Post, Query, Req, Res } from '@nestjs/common';
import type { Request, Response } from 'express';
import { z } from 'zod';
import { AuthService } from './auth';
import { Database } from './database';
import { BusinessError } from './errors';
import { EmployeeService, employeeOrder, employeeStates } from './employee-service';

const state = z.enum(employeeStates);
const password = z.string().max(128).refine(value => !value || value.length >= 6, '口令至少6位');
const createSchema = z.object({ code:z.string().min(1).max(64),name:z.string().trim().min(1).max(128),username:z.string().max(64).default(''),password:password.default(''),role:z.enum(['staff','leader']).default('staff') }).strict();
const editSchema = z.object({ name:z.string().trim().min(1).max(128),username:z.string().trim().min(1).max(64),status:state,language:z.enum(['','zh','ja']),role:z.enum(['staff','leader']),password:password.default(''),
  expected:z.object({username:z.string(),name:z.string(),status:state,language:z.string(),role:z.string()}).strict() }).strict();
const querySchema = z.object({ search:z.string().max(80).optional().default(''),status:state.optional(),page:z.coerce.number().int().min(1).max(10000).optional().default(1) }).strict();
function parse<T>(schema:z.ZodType<T>,body:unknown) { const parsed=schema.safeParse(body);if(!parsed.success)throw new BusinessError('VALIDATION','请检查员工资料及口令长度（至少6位）',400);return parsed.data; }
function identity(value:string){return parse(z.coerce.number().int().positive(),value);}
@Controller('employees')
export class EmployeesController {
 constructor(@Inject(AuthService) private readonly auth:AuthService,@Inject(Database) private readonly db:Database,@Inject(EmployeeService) private readonly service:EmployeeService){}
 @Get() async list(@Req() req:Request,@Query() query:unknown){
  await this.auth.require(req,{admin:true});
  const {search,status,page}=parse(querySchema,query);
  const result=await this.db.query(`WITH matching AS (
    SELECT id,username,display_name,person_code,role,status,is_active,lang,must_change_password
    FROM users WHERE role IN ('staff','leader') AND status<>'deleted' AND ($1='' OR username ILIKE $1 OR display_name ILIKE $1 OR person_code ILIKE $1) AND ($2::text IS NULL OR status=$2)
   ), paged AS (SELECT id,username,display_name AS "displayName",person_code AS "personCode",role,status,is_active AS "isActive",lang AS language,must_change_password AS "mustChangePassword" FROM matching ORDER BY ${employeeOrder} LIMIT 30 OFFSET $3)
   SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(paged) FROM paged),'[]'::json) AS items`,[search?'%'+search+'%':'',status??null,(page-1)*30]);
  return {...result.rows[0],page,pageSize:30};
 }
 @Post('suggest-username') async suggest(@Req() req:Request,@Body() body:unknown){
  await this.auth.require(req,{admin:true,write:true});
  const input=parse(z.object({name:z.string().trim().min(1).max(128),code:z.string().max(32)}).strict(),body);
  return this.service.propose(input.name,input.code);
 }
 @Post('create') async create(@Req() req:Request,@Body() body:unknown){await this.auth.require(req,{admin:true,write:true});return this.service.create(parse(createSchema,body));}
 @Get('accounts-export') async export(@Req() req:Request,@Query() query:unknown,@Res() response:Response){
  await this.auth.require(req,{admin:true});
  const input=parse(z.object({onlyActive:z.enum(['true','false']).default('false')}).strict(),query);
  const origin=(process.env.NEXT_PUBLIC_ORIGIN || '').replace(/\/$/,'');
  const bytes=await this.service.export(input.onlyActive==='true',origin);
  response.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
  response.setHeader('Content-Disposition',`attachment; filename="staff-accounts.xlsx"; filename*=UTF-8''${encodeURIComponent('员工登录名.xlsx')}`);
  response.send(bytes);
 }
 @Post('reset-all') async resetAll(@Req() req:Request,@Body() body:unknown){
  await this.auth.require(req,{admin:true,write:true});
  const input=parse(z.object({password:z.string().min(6).max(128),confirm:z.literal('RESET_ALL_STAFF')}).strict(),body);
  return this.service.reset(null,input.password);
 }
 @Post(':id/edit') async edit(@Req() req:Request,@Param('id') id:string,@Body() body:unknown){await this.auth.require(req,{admin:true,write:true});return this.service.edit(identity(id),parse(editSchema,body));}
 @Post(':id/reset') async reset(@Req() req:Request,@Param('id') id:string,@Body() body:unknown){
  await this.auth.require(req,{admin:true,write:true});const input=parse(z.object({password:z.string().min(6).max(128)}).strict(),body);return this.service.reset(identity(id),input.password);
 }
 @Post(':id/delete') async remove(@Req() req:Request,@Param('id') value:string,@Body() body:unknown){
  await this.auth.require(req,{admin:true,write:true});
  const input=parse(z.object({confirm:z.literal('DELETE_EMPLOYEE'),expectedStatus:state}).strict(),body);
  return this.service.remove(identity(value),input.expectedStatus);
 }
 @Post(':id/status') async status(@Req() req:Request,@Param('id') value:string,@Body() body:unknown){
  await this.auth.require(req,{admin:true,write:true});
  const id=identity(value),input=parse(z.object({status:state,expectedStatus:state}).strict(),body);
  return this.db.transaction(client=>this.service.setStatus(client,id,input.status,input.expectedStatus));
 }
}
