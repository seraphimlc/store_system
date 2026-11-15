import {Body,Controller,Get,Inject,Injectable,Post,Query,Req} from '@nestjs/common';
import type {Request,Response,NextFunction} from 'express';
import {z} from 'zod';
import {AuthService} from './auth';
import {McpOAuthService,OAuthError} from './mcp-oauth';
import {CONFIG,type AppConfig} from './config';
import {BusinessError} from './errors';

function parameters(query:unknown){
 if(!query||typeof query!=='object'||Array.isArray(query))throw new OAuthError('invalid_request','授权参数无效');
 const result:Record<string,string>={};for(const [key,value]of Object.entries(query)){if(typeof value!=='string')throw new OAuthError('invalid_request','授权参数不能重复');result[key]=value;}return result;
}
@Controller('mcp-oauth')
export class McpOAuthController{
 constructor(@Inject(AuthService)private readonly auth:AuthService,@Inject(McpOAuthService)private readonly oauth:McpOAuthService){}
 @Get('context')async context(@Req()req:Request,@Query()query:unknown){
  const {user}=await this.auth.require(req);
  try{const current=await this.oauth.authorization(parameters(query));
   if(user.status!=='active')throw new BusinessError('FORBIDDEN','当前账号状态不能授权MCP',403);
   return {client:{name:current.client.client_name||'MCP客户端',redirectUri:current.request.redirect_uri},scope:current.scope.split(','),user:{username:user.username,displayName:user.displayName,role:user.role},resource:this.oauth.resource()};
  }catch(error){if(error instanceof OAuthError)throw new BusinessError(error.code,error.message,error.status);throw error;}
 }
 @Post('authorize')async authorize(@Req()req:Request,@Body()body:unknown){
  const {user}=await this.auth.require(req,{write:true});const parsed=z.object({request:z.record(z.string(),z.string()),approved:z.boolean()}).strict().safeParse(body);
  if(!parsed.success)throw new BusinessError('VALIDATION','授权确认信息无效',400);
  try{return await this.oauth.authorize(user.id,parsed.data.request,parsed.data.approved);}catch(error){if(error instanceof OAuthError)throw new BusinessError(error.code,error.message,error.status);throw error;}
 }
}

@Injectable()
export class McpOAuthHttp{
 private readonly windows=new Map<string,{count:number;until:number}>();
 constructor(@Inject(McpOAuthService)private readonly oauth:McpOAuthService,@Inject(CONFIG)private readonly config:AppConfig){}
 private throttle(req:Request){
  const now=Date.now(),key=req.ip??req.socket.remoteAddress??'unknown',old=this.windows.get(key);
  if(old&&old.until>now){if(old.count>=120)throw new OAuthError('temporarily_unavailable','请求过于频繁，请稍后重试',429);old.count++;}
  else{if(this.windows.size>=1000)this.windows.delete(this.windows.keys().next().value!);this.windows.set(key,{count:1,until:now+60000});}
 }
 private async body(req:Request){
  const type=(req.headers['content-type']??'').split(';')[0].trim().toLowerCase();
  if(!['application/json','application/x-www-form-urlencoded'].includes(type))throw new OAuthError('invalid_request','需要JSON或表单内容类型',415);
  if(req.body!==undefined){if(!req.body||typeof req.body!=='object'||Array.isArray(req.body))throw new OAuthError('invalid_request','请求内容无效');return req.body;}
  const chunks:Buffer[]=[];let size=0;
  for await(const chunk of req){const bytes=Buffer.isBuffer(chunk)?chunk:Buffer.from(chunk);size+=bytes.length;if(size>16384)throw new OAuthError('invalid_request','OAuth请求超过16KiB',413);chunks.push(bytes);}
  const text=Buffer.concat(chunks).toString('utf8');
  if(type==='application/json'){try{const value=JSON.parse(text);if(!value||typeof value!=='object'||Array.isArray(value))throw Error();return value;}catch{throw new OAuthError('invalid_request','JSON内容无效');}}
  const form=new URLSearchParams(text),result:Record<string,string>={};for(const [key,value]of form){if(Object.hasOwn(result,key))throw new OAuthError('invalid_request','表单参数不能重复');result[key]=value;}return result;
 }
 async handle(req:Request,res:Response,next:NextFunction){
  const metadata=req.path==='/.well-known/oauth-authorization-server';
  if(!metadata&&!['/oauth/register','/oauth/authorize','/oauth/token','/oauth/revoke'].includes(req.path)){next();return;}
  res.setHeader('Cache-Control','no-store');res.setHeader('Pragma','no-cache');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');
  // OAuth public native clients may call protocol endpoints cross-origin; cookies confer no authority here.
  if(req.path!=='/oauth/authorize'){res.setHeader('Access-Control-Allow-Origin','*');res.setHeader('Access-Control-Allow-Methods','GET,POST,OPTIONS');res.setHeader('Access-Control-Allow-Headers','Content-Type');}
  if(req.method==='OPTIONS'&&req.path!=='/oauth/authorize'){res.status(204).end();return;}
  try{
   if(metadata){if(req.method!=='GET')throw new OAuthError('invalid_request','仅支持GET',405);res.json(this.oauth.metadata());return;}
   this.throttle(req);
   if(req.path==='/oauth/authorize'){
    if(req.method!=='GET')throw new OAuthError('invalid_request','请通过授权页面确认',405);
    const query=parameters(req.query);await this.oauth.authorization(query);
    const target=new URL(this.config.mcpAuthorizationUiUrl??(this.config.cookieSecure?this.oauth.issuer()+'/admin/mcp-authorize':'http://127.0.0.1:8001/admin/mcp-authorize'));
    target.search=new URLSearchParams(query).toString();res.redirect(303,target.href);return;
   }
   if(req.method!=='POST')throw new OAuthError('invalid_request','仅支持POST',405);
   const body=await this.body(req);
   if(req.path==='/oauth/register'){res.status(201).json(await this.oauth.register(body));return;}
   if(req.path==='/oauth/token'){res.json(await this.oauth.exchange(body));return;}
   await this.oauth.revoke(body);res.status(200).json({});
  }catch(error){
   if(error instanceof OAuthError)res.status(error.status).json({error:error.code,error_description:error.message});
   else res.status(500).json({error:'server_error',error_description:'授权服务暂不可用'});
  }
 }
}
