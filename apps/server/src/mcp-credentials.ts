import {Body,Controller,Get,Inject,Injectable,Param,Post,Req} from '@nestjs/common';
import type {Request} from 'express';
import {createHash,randomBytes} from 'node:crypto';
import type {PoolClient} from 'pg';
import {z} from 'zod';
import {Database} from './database';
import {AuthService} from './auth';
import {BusinessError} from './errors';
import {McpOAuthService} from './mcp-oauth';
const id=z.coerce.number().int().positive().max(2147483647);
const issueSchema=z.object({name:z.string().trim().min(1).max(128),scopes:z.enum(['read','read,write']),days:z.union([z.literal(90),z.null()]).default(90)}).strict();
function identity(value:string){const p=id.safeParse(value);if(!p.success)throw new BusinessError('VALIDATION','凭据或人员编号无效',400);return p.data;}
@Injectable()
export class McpCredentialsService{
 constructor(@Inject(Database)private readonly db:Database,@Inject(McpOAuthService)private readonly oauth:McpOAuthService){}
 private async admin(client:PoolClient,actorId:number,lock=true){if(!(await client.query(`SELECT id FROM users WHERE id=$1 AND role='admin' AND is_active=true AND status='active' AND must_change_password=false ${lock?'FOR SHARE':''}`,[actorId])).rowCount)throw new BusinessError('FORBIDDEN','当前管理员不可管理凭据',403);}
 private async target(client:PoolClient,userId:number,write=false){const row=(await client.query(`SELECT id,username,display_name,role,status,is_active,must_change_password FROM users WHERE id=$1 AND status<>'deleted' ${write?'FOR SHARE':''}`,[userId])).rows[0];if(!row)throw new BusinessError('NOT_FOUND','账号不存在',404);return row;}
 async list(actorId:number,userId:number){return this.db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');await this.admin(client,actorId,false);const user=await this.target(client,userId);
  const rows=(await client.query('SELECT id,name,token_prefix,scopes,created_at,last_used_at,revoked_at,expires_at FROM api_tokens WHERE user_id=$1 ORDER BY id DESC',[userId])).rows;
  const now=Date.now();return {user:{id:user.id,username:user.username,displayName:user.display_name,role:user.role,status:user.status},items:rows.map(row=>({...row,status:row.revoked_at?'revoked':row.expires_at&&row.expires_at.getTime()<=now?'expired':!user.is_active||user.status!=='active'||user.must_change_password?'user_inactive':'active'}))};
 });}
 async issue(actorId:number,userId:number,input:unknown){const p=issueSchema.safeParse(input);if(!p.success)throw new BusinessError('VALIDATION','名称、权限或有效期无效',400);
  // Do not allow personal token labels to impersonate an OAuth family or legacy reserved label.
  if(/^oauth(?::|\s|$)/i.test(p.data.name))throw new BusinessError('VALIDATION','该凭据名称为授权协议保留',400);
  return this.db.transaction(async client=>{await this.admin(client,actorId);const user=await this.target(client,userId,true);if(!user.is_active||user.status!=='active'||user.must_change_password)throw new BusinessError('ACCOUNT_UNAVAILABLE','只能向已改初始密码的在岗账号签发凭据',409);
   const raw=randomBytes(32).toString('base64url');const token=(await client.query(`INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at,expires_at)
    VALUES($1,$2,$3,$4,$5,timezone('UTC',now()),CASE WHEN $6::int IS NULL THEN NULL ELSE timezone('UTC',now())+($6::int*interval '1 day') END)
    RETURNING id,name,token_prefix,scopes,created_at,expires_at`,[userId,p.data.name,raw.slice(0,8),createHash('sha256').update(raw).digest('hex'),p.data.scopes,p.data.days])).rows[0];
   return {token:raw,credential:token,note:'明文仅在本次响应显示，请安全保存；关闭后无法再次查看'};
  });
 }
 async revoke(actorId:number,userId:number,tokenId:number){return this.db.transaction(async client=>{
  const candidate=(await client.query('SELECT name FROM api_tokens WHERE id=$1 AND user_id=$2',[tokenId,userId])).rows[0];
  const clientKey=/^OAuth:([0-9]+):/.exec(candidate?.name??'');
  if(clientKey){const registered=(await client.query<{client_id:string}>('SELECT client_id FROM oauth_clients WHERE id=$1',[Number(clientKey[1])])).rows[0];if(registered)await client.query("SELECT pg_advisory_xact_lock(hashtext('oauth-client'),hashtext($1))",[registered.client_id]);}
  await this.admin(client,actorId);await this.target(client,userId);
  const row=(await client.query('SELECT id,name FROM api_tokens WHERE id=$1 AND user_id=$2 FOR UPDATE',[tokenId,userId])).rows[0];if(!row)throw new BusinessError('NOT_FOUND','凭据不存在或不属于该账号',404);
  const family=/^OAuth:([0-9]+):([a-f0-9]{64})(?::[a-f0-9]{16})?$/.exec(row.name);
  if(family)await this.oauth.revokeAccessFamily(client,Number(family[1]),userId,family[2]);
  else if(/^OAuth(?:\s|$)/.test(row.name)){
   // Old access tokens have no family relation: revoke all old refresh grants for this account rather than promise incomplete revocation.
   await client.query("UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE user_id=$1",[userId]);
  }
  await client.query("UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE id=$1",[tokenId]);return {ok:true};
 });}
}
@Controller('mcp-credentials')
export class McpCredentialsController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(McpCredentialsService)private readonly credentials:McpCredentialsService){}
 @Get(':userId')async list(@Req()req:Request,@Param('userId')value:string){const {user}=await this.auth.require(req,{admin:true});return this.credentials.list(user.id,identity(value));}
 @Post(':userId')async issue(@Req()req:Request,@Param('userId')value:string,@Body()body:unknown){const {user}=await this.auth.require(req,{admin:true,write:true});return this.credentials.issue(user.id,identity(value),body);}
 @Post(':userId/:tokenId/revoke')async revoke(@Req()req:Request,@Param('userId')value:string,@Param('tokenId')token:string){const {user}=await this.auth.require(req,{admin:true,write:true});return this.credentials.revoke(user.id,identity(value),identity(token));}
}
