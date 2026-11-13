/** Explicit maintenance only. Never runs from API startup or normal release commands. */
import {createRequire} from 'node:module';
import {readFile,writeFile,unlink} from 'node:fs/promises';
import {randomBytes} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url));
export async function rotateWeakAccounts(client,{apply=false,credentialFile}={}){
 const {verify,hash}=require('@node-rs/argon2');
 const rows=(await client.query("SELECT id,username,password_hash FROM users WHERE status<>'deleted' ORDER BY id")).rows;
 const weak=[];for(const row of rows){for(const candidate of ['demo123','admin','password','123456'])if(await verify(row.password_hash,candidate).catch(()=>false)){weak.push(row);break;}}
 if(!apply)return {affected:weak.length,applied:false};
 if(!credentialFile)throw Error('A private --credential-file is required for one-time delivery');
 const credentials=await Promise.all(weak.map(async row=>{const password=randomBytes(18).toString('base64url');return {id:row.id,username:row.username,temporaryPassword:password,hash:await hash(password),old:row.password_hash};}));
 // Exclusive private file creation precedes commit. It is removed if any database mutation fails.
 await writeFile(credentialFile,JSON.stringify(credentials.map(({hash,old,...item})=>item),null,2)+'\n',{mode:0o600,flag:'wx'});
 try{
  await client.query('BEGIN');
  for(const row of credentials){const result=await client.query('UPDATE users SET password_hash=$1,must_change_password=true WHERE id=$2 AND password_hash=$3',[row.hash,row.id,row.old]);if(result.rowCount!==1)throw Error('Account changed; no passwords were rotated');}
  const ids=credentials.map(row=>row.id);
  await client.query('UPDATE auth_sessions SET revoked_at=now() WHERE user_id=ANY($1::int[]) AND revoked_at IS NULL',[ids]);
  await client.query("UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE user_id=ANY($1::int[])",[ids]);
  await client.query("UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now())) WHERE user_id=ANY($1::int[])",[ids]);
  await client.query('DELETE FROM oauth_codes WHERE user_id=ANY($1::int[]) AND used_at IS NULL',[ids]);
  await client.query('COMMIT');
 }catch(error){await client.query('ROLLBACK');await unlink(credentialFile);throw error;}
 return {affected:credentials.length,applied:true,credentialFile};
}
async function main(){
 const [action,...args]=process.argv.slice(2);if(!['schema','rotate-weak','revoke-all','prune'].includes(action))throw Error('Usage: security-maintenance.mjs schema|rotate-weak|revoke-all|prune [--apply --confirm-database=NAME] [--credential-file=PATH]');
 const allowed=args.every(arg=>arg==='--apply'||arg.startsWith('--confirm-database=')||arg.startsWith('--credential-file='));if(!allowed)throw Error('Unknown maintenance option');
 const url=new URL(process.env.NEXT_DATABASE_URL||''),database=decodeURIComponent(url.pathname.slice(1)),apply=args.includes('--apply');
 if(apply&&!args.includes('--confirm-database='+database))throw Error('Mutation requires --confirm-database matching the configured database');
 const {Client}=require('pg'),client=new Client({connectionString:url.href,application_name:'store-security-maintenance'});await client.connect();
 try{
  if(action==='schema'){if(apply)await client.query(await readFile(new URL('../deploy-next/migrations/001-auth-security.sql',import.meta.url),'utf8'));console.log(JSON.stringify({action,database,applied:apply}));}
  if(action==='rotate-weak')console.log(JSON.stringify(await rotateWeakAccounts(client,{apply,credentialFile:args.find(arg=>arg.startsWith('--credential-file='))?.slice(18)})));
  if(action==='revoke-all'){
   if(apply){await client.query('BEGIN');try{await client.query('UPDATE auth_sessions SET revoked_at=now() WHERE revoked_at IS NULL');await client.query("UPDATE api_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now()))");await client.query("UPDATE oauth_refresh_tokens SET revoked_at=coalesce(revoked_at,timezone('UTC',now()))");await client.query('DELETE FROM oauth_codes WHERE used_at IS NULL');await client.query('COMMIT');}catch(error){await client.query('ROLLBACK');throw error;}}
   console.log(JSON.stringify({action,database,applied:apply}));
  }
  if(action==='prune'){
   if(apply){await client.query("DELETE FROM auth_sessions WHERE expires_at<now()-interval '1 day'");await client.query("UPDATE security_export_events SET finished_at=now(),ok=false WHERE finished_at IS NULL AND created_at<now()-interval '15 minutes'");}
   console.log(JSON.stringify({action,database,applied:apply}));
  }
 }finally{await client.end();}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)main().catch(()=>{console.error('Security maintenance failed; check database, migration and explicit mutation options. Credentials are never logged.');process.exitCode=1;});
