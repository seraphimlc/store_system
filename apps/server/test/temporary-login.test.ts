import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {hash} from '@node-rs/argon2';
import type {Request,Response} from 'express';
import {AuthService} from '../src/auth';
import type {Database} from '../src/database';
const passwordHash=hash('original-fixture-password');
async function fixture(role='staff',mustChange=false){
 const row={id:1,username:'fixture',password_hash:await passwordHash,display_name:'Fixture',person_code:null,role,status:'active',is_active:true,must_change_password:mustChange,lang:'zh'};
 const sessions=new Map<string,boolean>();
 const db={query:async(sql:string,args:unknown[])=>{
  if(sql.startsWith('INSERT INTO auth_sessions')){sessions.set(String(args[0]),true);return {rows:[]};}
  if(sql.startsWith('UPDATE auth_sessions')){sessions.set(String(args[0]),false);return {rows:[]};}
  if(sql.includes('JOIN auth_sessions')&&!sessions.get(String(args[1])))return {rows:[]};
  return {rows:[row]};
 }} as unknown as Database;
 const config={databaseUrl:'',sessionSecret:'isolated-test-secret-with-40-characters',cookieSecure:false,host:'127.0.0.1',port:0};
 const auth=new AuthService(db,config);let cookie='',cleared=false;
 const response={cookie:(name:string,value:string)=>{cookie=`${name}=${value}`;},clearCookie:()=>{cleared=true;}} as unknown as Response;
 const initial={ip:'127.0.0.1',headers:{},get:()=>''} as unknown as Request;
 const login=()=>auth.login('fixture','original-fixture-password',initial,response);
 return {auth,db,config,initial,response,login,request:(csrf='')=>({...initial,headers:{cookie},get:()=>csrf} as Request),cleared:()=>cleared};
}
test('retired universal/default passwords cannot authenticate any role',async()=>{
 for(const role of ['admin','leader','staff']){const f=await fixture(role);for(const password of ['superAdmin7489757','demo123'])await assert.rejects(f.auth.login('fixture',password,f.initial,f.response),{status:401});assert.equal((await f.login()).user.role,role);}
});
test('logout revokes a copied cookie across AuthService instances, preserves CSRF and other sessions',async()=>{
 const f=await fixture(),first=await f.login();let copied=f.request(first.csrfToken);
 await f.login();const other=f.request();await assert.rejects(f.auth.logout(copied={...copied,get:()=>''} as Request,f.response),{status:403});
 copied.get=(()=>first.csrfToken) as Request['get'];await f.auth.logout(copied,f.response);assert.equal(f.cleared(),true);
 await assert.rejects(new AuthService(f.db,f.config).require(copied),{status:401});assert.equal((await f.auth.require(other)).user.id,1);
});
test('temporary sessions retain first-password-change and CSRF boundaries',async()=>{
 const f=await fixture('staff',true),session=await f.login();await assert.rejects(f.auth.require(f.request()),{status:403});
 assert.equal((await f.auth.require(f.request(),{allowPasswordChange:true})).user.mustChangePassword,true);
 await f.auth.logout(f.request(session.csrfToken),f.response);await assert.rejects(f.auth.require(f.request(),{allowPasswordChange:true}),{status:401});
});
