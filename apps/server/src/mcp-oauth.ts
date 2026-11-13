import {Inject,Injectable} from '@nestjs/common';
import {createHash,randomBytes,timingSafeEqual} from 'node:crypto';
import type {PoolClient} from 'pg';
import {z} from 'zod';
import {Database} from './database';
import {CONFIG,type AppConfig} from './config';

export class OAuthError extends Error{
 constructor(readonly code:string,message:string,readonly status=400){super(message);}
}
const sha=(value:string)=>createHash('sha256').update(value).digest('hex');
const rawToken=()=>randomBytes(32).toString('base64url');
function equal(a:string,b:string){const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);}
export function validOAuthRedirect(value:string){
 if(!value||value.length>512||/[\u0000-\u0020\u007f\\]/.test(value))return false;
 try{const url=new URL(value);if(url.hash||url.username||url.password)return false;
  if(url.protocol==='https:')return !!url.hostname;
  if(url.protocol==='http:')return ['localhost','127.0.0.1','[::1]'].includes(url.hostname);
  // Installed native clients may register a custom URI; never executable or local-file schemes.
  return !['javascript:','data:','file:','vbscript:','blob:','about:','mailto:','tel:','sms:','intent:'].includes(url.protocol)&&/^[a-z][a-z0-9+.-]*:$/.test(url.protocol)&&!!(url.hostname||url.pathname);
 }catch{return false;}
}
function scope(value:unknown,base?:string){
 if(value==null||value==='')return base??'read';
 if(typeof value!=='string'||value.length>64)throw new OAuthError('invalid_scope','权限范围无效');
 const requested=[...new Set(value.replaceAll(',',' ').split(/\s+/).filter(Boolean))];
 if(!requested.length||requested.some(s=>!['read','write'].includes(s)))throw new OAuthError('invalid_scope','只支持read/write');
 if(!requested.includes('read'))requested.unshift('read');
 const output=['read',...(requested.includes('write')?['write']:[])].join(',');
 if(base&&output.split(',').some(s=>!base.split(',').includes(s)))throw new OAuthError('invalid_scope','请求的权限超过已批准范围');
 return output;
}
const registerSchema=z.object({scope:z.string().max(64).optional(),client_name:z.string().trim().max(128).default('MCP客户端'),redirect_uris:z.array(z.string().refine(validOAuthRedirect)).min(1).max(10),token_endpoint_auth_method:z.literal('none').optional(),grant_types:z.array(z.enum(['authorization_code','refresh_token'])).optional(),response_types:z.array(z.literal('code')).optional(),client_uri:z.string().max(1024).optional(),logo_uri:z.string().max(1024).optional(),contacts:z.array(z.string().max(255)).max(10).optional(),policy_uri:z.string().max(1024).optional(),tos_uri:z.string().max(1024).optional()}).strict();
const authorizeSchema=z.object({client_id:z.string().min(1).max(128),redirect_uri:z.string().max(512),response_type:z.literal('code'),code_challenge:z.string().regex(/^[A-Za-z0-9_-]{43}$/),code_challenge_method:z.literal('S256'),scope:z.string().max(64).optional(),state:z.string().max(2048).optional(),resource:z.string().max(1024).optional()}).strict();
type User={id:number;role:string;status:string;is_active:boolean;must_change_password:boolean};
type OAuthClient={id:number;client_id:string;client_name:string;redirect_uris:string[];client_secret_hash:string|null;allowed_scopes:string;approved:boolean};
const accessSeconds=24*3600,refreshSeconds=90*24*3600;
@Injectable()
export class McpOAuthService{
 constructor(@Inject(Database)private readonly db:Database,@Inject(CONFIG)private readonly config:AppConfig){}
 resource(){return this.config.mcpPublicUrl??`http://${this.config.host}:${this.config.port}/mcp`;}
 issuer(){return new URL(this.resource()).origin;}
 metadata(){const issuer=this.issuer();return {issuer,authorization_endpoint:issuer+'/oauth/authorize',token_endpoint:issuer+'/oauth/token',registration_endpoint:issuer+'/oauth/register',revocation_endpoint:issuer+'/oauth/revoke',response_types_supported:['code'],grant_types_supported:['authorization_code','refresh_token'],token_endpoint_auth_methods_supported:['none'],code_challenge_methods_supported:['S256'],scopes_supported:['read','write']};}
 private resourceCheck(value:unknown){if(value!==undefined&&value!==this.resource())throw new OAuthError('invalid_target','授权资源与MCP入口不一致');}
 private async client(client:Pick<Database,'query'>,clientId:string){const row=(await client.query<OAuthClient>(`SELECT c.id,c.client_id,c.client_name,c.redirect_uris,c.client_secret_hash,p.allowed_scopes,p.approved FROM oauth_clients c JOIN oauth_client_policies p ON p.client_id=c.id WHERE c.client_id=$1`,[clientId])).rows[0];if(!row||row.client_secret_hash)throw new OAuthError('invalid_client','未注册的公共客户端');if(!row.approved)throw new OAuthError('unauthorized_client','客户端尚未获管理员批准，请联系管理员',403);return row;}
 private async user(client:PoolClient,id:number){const row=(await client.query<User>("SELECT id,role,status,is_active,must_change_password FROM users WHERE id=$1 FOR SHARE",[id])).rows[0];if(!row||!row.is_active||row.status!=='active'||row.must_change_password||!['admin','leader','staff'].includes(row.role))throw new OAuthError('invalid_grant','账号当前不可授权，请登录检查状态');return row;}
 async register(value:unknown){const p=registerSchema.safeParse(value);if(!p.success)throw new OAuthError('invalid_client_metadata','需要有效回调地址和公共客户端元数据');
  const requestedScope=scope(p.data.scope),id=randomBytes(18).toString('base64url'),uris=[...new Set(p.data.redirect_uris)];
  const approved=uris.every(uri=>this.config.oauthTrustedRedirectUris?.includes(uri));
  await this.db.transaction(async client=>{
   const row=(await client.query<{id:number}>("INSERT INTO oauth_clients(client_id,client_name,redirect_uris,created_at) VALUES($1,$2,$3,timezone('UTC',now())) RETURNING id",[id,p.data.client_name,JSON.stringify(uris)])).rows[0];
   await client.query('INSERT INTO oauth_client_policies(client_id,allowed_scopes,approved) VALUES($1,$2,$3)',[row.id,requestedScope,approved]);
  });
  return {client_id:id,client_name:p.data.client_name,redirect_uris:uris,scope:requestedScope.replaceAll(',',' '),approval_required:!approved,token_endpoint_auth_method:'none',grant_types:['authorization_code','refresh_token'],response_types:['code']};
 }
 async clients(){return (await this.db.query('SELECT c.client_id,c.client_name,c.redirect_uris,p.allowed_scopes,p.approved FROM oauth_clients c JOIN oauth_client_policies p ON p.client_id=c.id ORDER BY c.id DESC LIMIT 100')).rows;}
 async approve(clientId:string,approved:boolean,allowedScopes:string){
  const permissions=scope(allowedScopes);
  return this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock(hashtext('oauth-client'),hashtext($1))",[clientId]);
   const row=(await client.query<{id:number}>('SELECT id FROM oauth_clients WHERE client_id=$1 FOR UPDATE',[clientId])).rows[0];
   if(!row)throw new OAuthError('invalid_client','客户端不存在',404);
   await client.query('UPDATE oauth_client_policies SET approved=$2,allowed_scopes=$3 WHERE client_id=$1',[row.id,approved,permissions]);
   await client.query("UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE name LIKE $1",[`OAuth:${row.id}:%`]);
   await client.query("UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE client_id=$1",[row.id]);
   await client.query('DELETE FROM oauth_codes WHERE client_id=$1 AND used_at IS NULL',[row.id]);
   return {client_id:clientId,approved,scope:permissions.replaceAll(',',' ')};
  });
 }

 async authorization(value:unknown,client:Pick<Database,'query'>=this.db){const p=authorizeSchema.safeParse(value);if(!p.success)throw new OAuthError('invalid_request','授权请求、PKCE或回调参数无效');this.resourceCheck(p.data.resource);const registered=await this.client(client,p.data.client_id);
  if(!validOAuthRedirect(p.data.redirect_uri)||!registered.redirect_uris.includes(p.data.redirect_uri))throw new OAuthError('invalid_request','回调地址与注册值不一致');
  return {request:p.data,client:registered,scope:scope(p.data.scope,registered.allowed_scopes)};
 }
 async authorize(userId:number,value:unknown,approved:boolean){
  return this.db.transaction(async client=>{
   const parsed=authorizeSchema.safeParse(value);if(!parsed.success)throw new OAuthError('invalid_request','授权参数无效');
   await client.query("SELECT pg_advisory_xact_lock(hashtext('oauth-client'),hashtext($1))",[parsed.data.client_id]);
   const current=await this.authorization(value,client);await this.user(client,userId);
   const redirect=new URL(current.request.redirect_uri);if(current.request.state!==undefined)redirect.searchParams.set('state',current.request.state);
   if(!approved){redirect.searchParams.set('error','access_denied');return {redirect:redirect.href};}
   const raw=rawToken();await client.query(`INSERT INTO oauth_codes(code_hash,client_id,user_id,redirect_uri,code_challenge,code_challenge_method,scope,expires_at,created_at)
    VALUES($1,$2,$3,$4,$5,'S256',$6,timezone('UTC',now())+interval '5 minutes',timezone('UTC',now()))`,[sha(raw),current.client.id,userId,current.request.redirect_uri,current.request.code_challenge,current.scope]);
   redirect.searchParams.set('code',raw);return {redirect:redirect.href};
  });
 }
 // Refresh generations share a family digest in an existing field; no new columns and no raw tokens stored.
 private accessName(clientId:number,family:string){return `OAuth:${clientId}:${family}`;}
 private async issue(client:PoolClient,registered:OAuthClient,user:User,permissions:string,family?:string){
  const access=rawToken(),refresh=rawToken(),hash=sha(refresh),familyId=family??hash;
  const accessRow=(await client.query<{id:number}>(`INSERT INTO api_tokens(user_id,name,token_prefix,token_hash,scopes,created_at,expires_at)
   VALUES($1,$2,$3,$4,$5,timezone('UTC',now()),timezone('UTC',now())+interval '24 hours') RETURNING id`,[user.id,this.accessName(registered.id,familyId),access.slice(0,8),sha(access),permissions])).rows[0];
  await client.query(`INSERT INTO oauth_refresh_tokens(token_hash,client_id,user_id,scope,expires_at,created_at)
   VALUES($1,$2,$3,$4,timezone('UTC',now())+interval '90 days',timezone('UTC',now()))`,[hash,registered.id,user.id,permissions]);
  // On rotation, access names associate the refresh generation to the retained family id via a digest tag.
  if(family)await client.query('UPDATE api_tokens SET name=$2 WHERE id=$1',[accessRow.id,this.accessName(registered.id,familyId)+':'+hash.slice(0,16)]);
  await client.query("UPDATE oauth_clients SET last_used_at=timezone('UTC',now()) WHERE id=$1",[registered.id]);
  return {accessId:accessRow.id,response:{access_token:access,token_type:'Bearer',expires_in:accessSeconds,refresh_token:refresh,scope:permissions.replaceAll(',',' ')},family:familyId};
 }
 async revokeAccessFamily(client:PoolClient,clientId:number,userId:number,family:string){
  await client.query("UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE user_id=$1 AND (name=$2 OR left(name,length($2)+1)=$2||':')",[userId,this.accessName(clientId,family)]);
  // Family of current refresh hashes is encoded in its corresponding access name; recover only digests.
  const rows=(await client.query<{name:string}>('SELECT name FROM api_tokens WHERE user_id=$1 AND (name=$2 OR left(name,length($2)+1)=$2||\':\')',[userId,this.accessName(clientId,family)])).rows;
  const prefixes=rows.map(row=>row.name.split(':')[3]).filter(Boolean);
  await client.query("UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE client_id=$1 AND user_id=$2 AND (token_hash=$3 OR left(token_hash,16)=ANY($4::text[]))",[clientId,userId,family,prefixes]);
 }
 private async family(client:PoolClient,clientId:number,userId:number,hash:string){
  const tag=':'+hash.slice(0,16),prefix=`OAuth:${clientId}:`;
  const row=(await client.query<{name:string}>("SELECT name FROM api_tokens WHERE user_id=$1 AND (name=$2 OR (left(name,length($3))=$3 AND right(name,length($4))=$4)) ORDER BY id LIMIT 1",[userId,prefix+hash,prefix,tag])).rows[0];
  return row?.name.split(':')[2]??hash;
 }
 async exchange(value:unknown){
  const base=z.object({grant_type:z.enum(['authorization_code','refresh_token']),client_id:z.string().min(1).max(128),code:z.string().min(1).max(512).optional(),redirect_uri:z.string().max(512).optional(),code_verifier:z.string().max(128).optional(),refresh_token:z.string().min(1).max(512).optional(),scope:z.string().max(64).optional(),resource:z.string().max(1024).optional()}).strict().safeParse(value);
  if(!base.success)throw new OAuthError('invalid_request','Token请求格式无效');const input=base.data;this.resourceCheck(input.resource);
  // Return failure after committing replay revocations; throwing inside a transaction would undo them.
  const outcome=await this.db.transaction(async client=>{
   await client.query("SELECT pg_advisory_xact_lock(hashtext('oauth-client'),hashtext($1))",[input.client_id]);
   const registered=await this.client(client,input.client_id);
   if(input.grant_type==='authorization_code'){
    if(!input.code||!input.redirect_uri||!input.code_verifier||!/^[A-Za-z0-9._~-]{43,128}$/.test(input.code_verifier))throw new OAuthError('invalid_grant','授权码交换参数无效');
    const row=(await client.query<Record<string,any>>('SELECT * FROM oauth_codes WHERE code_hash=$1 FOR UPDATE',[sha(input.code)])).rows[0];
    if(!row||row.client_id!==registered.id||row.redirect_uri!==input.redirect_uri||!registered.redirect_uris.includes(input.redirect_uri))throw new OAuthError('invalid_grant','授权码或回调无效');
    const challenge=createHash('sha256').update(input.code_verifier).digest('base64url');
    if(row.code_challenge_method!=='S256'||!equal(challenge,row.code_challenge))throw new OAuthError('invalid_grant','PKCE校验失败');
    if(row.used_at){if(row.access_token_id){const access=(await client.query<{name:string;user_id:number}>('SELECT name,user_id FROM api_tokens WHERE id=$1',[row.access_token_id])).rows[0];if(access){await client.query("UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE id=$1",[row.access_token_id]);const family=access.name.split(':')[2];if(/^[a-f0-9]{64}$/.test(family??''))await this.revokeAccessFamily(client,registered.id,access.user_id,family);else await client.query("UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE client_id=$1 AND user_id=$2",[registered.id,access.user_id]);}}
     return {error:new OAuthError('invalid_grant','授权码已使用，请重新连接')};}
    if(row.expires_at.getTime()<=Date.now())throw new OAuthError('invalid_grant','授权码已过期');
    const user=await this.user(client,row.user_id),permissions=scope(scope(input.scope,row.scope),registered.allowed_scopes),issued=await this.issue(client,registered,user,permissions);
    await client.query("UPDATE oauth_codes SET used_at=timezone('UTC',now()),access_token_id=$2 WHERE id=$1",[row.id,issued.accessId]);return {response:issued.response};
   }
   if(!input.refresh_token)throw new OAuthError('invalid_request','缺少refresh_token');const hash=sha(input.refresh_token);
   const row=(await client.query<Record<string,any>>('SELECT * FROM oauth_refresh_tokens WHERE token_hash=$1 FOR UPDATE',[hash])).rows[0];
   if(!row||row.client_id!==registered.id)throw new OAuthError('invalid_grant','刷新凭据无效');
   const family=await this.family(client,registered.id,row.user_id,hash);
   if(row.revoked_at){await this.revokeAccessFamily(client,registered.id,row.user_id,family);return {error:new OAuthError('invalid_grant','刷新凭据已轮换或撤销，请重新连接')};}
   if(row.expires_at.getTime()<=Date.now())throw new OAuthError('invalid_grant','刷新凭据已过期');
   const user=await this.user(client,row.user_id),permissions=scope(scope(input.scope,row.scope),registered.allowed_scopes);
   await client.query("UPDATE oauth_refresh_tokens SET revoked_at=timezone('UTC',now()),last_used_at=timezone('UTC',now()) WHERE id=$1",[row.id]);
   return {response:(await this.issue(client,registered,user,permissions,family)).response};
  });
  if(outcome.error)throw outcome.error;return outcome.response!;
 }
 async revoke(value:unknown){const p=z.object({client_id:z.string().min(1).max(128),token:z.string().min(1).max(512),token_type_hint:z.enum(['access_token','refresh_token']).optional()}).strict().safeParse(value);if(!p.success)throw new OAuthError('invalid_request','撤销请求无效');
  await this.db.transaction(async client=>{await client.query("SELECT pg_advisory_xact_lock(hashtext('oauth-client'),hashtext($1))",[p.data.client_id]);const registered=await this.client(client,p.data.client_id),hash=sha(p.data.token);
   const refresh=(await client.query<Record<string,any>>('SELECT user_id FROM oauth_refresh_tokens WHERE token_hash=$1 AND client_id=$2 FOR UPDATE',[hash,registered.id])).rows[0];
   if(refresh){const family=await this.family(client,registered.id,refresh.user_id,hash);await this.revokeAccessFamily(client,registered.id,refresh.user_id,family);await client.query("UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE token_hash=$1",[hash]);}
   const access=(await client.query<{id:number;name:string;user_id:number}>('SELECT id,name,user_id FROM api_tokens WHERE token_hash=$1 FOR UPDATE',[hash])).rows[0];
   if(access?.name.startsWith(`OAuth:${registered.id}:`)){await this.revokeAccessFamily(client,registered.id,access.user_id,access.name.split(':')[2]);}
  });return {};
 }
}
