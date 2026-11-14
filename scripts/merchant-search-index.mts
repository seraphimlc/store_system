/** Explicit maintenance only. Never invoked by API boot/dev; do not print connection strings. */
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const {Client}=createRequire(new URL('../apps/server/package.json',import.meta.url))('pg');
import {buildMerchantSearchIndex} from '../apps/server/src/merchant-search-index';

const [action,...flags]=process.argv.slice(2);
if(!['status','schema','rebuild'].includes(action)||flags.some(f=>f!=='--apply'&&!f.startsWith('--confirm-database=')))throw Error('Usage: merchant-search-index.mts status | schema|rebuild --apply --confirm-database=<database>');
const url=new URL(process.env.NEXT_DATABASE_URL??'');if(!['postgres:','postgresql:'].includes(url.protocol))throw Error('NEXT_DATABASE_URL must be PostgreSQL');
const database=decodeURIComponent(url.pathname.slice(1)),apply=flags.includes('--apply'),confirmation=flags.find(f=>f.startsWith('--confirm-database='))?.split('=').slice(1).join('=');
if(action!=='status'&&(!apply||confirmation!==database||!database))throw Error('Index maintenance requires --apply and exact --confirm-database; no database operation performed');
const client=new Client({connectionString:url.href,application_name:'merchant-search-maintenance',statement_timeout:action==='rebuild'?0:300000});
try{
 await client.connect();
 if(action==='status'){
  await client.query('BEGIN READ ONLY');const exists=(await client.query("SELECT to_regclass('public.bd_merchant_search_active') AS table_name")).rows[0].table_name;
  const current=exists?(await client.query('SELECT g.id,g.strategy_version,g.source_relation,g.source_sha256,g.document_count,g.group_count,g.ready,g.created_at FROM bd_merchant_search_active a JOIN bd_merchant_search_generation g ON g.id=a.generation_id')).rows[0]??null:null;
  console.log(JSON.stringify({database,current}));await client.query('ROLLBACK');
 }else{
  await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ; SET LOCAL lock_timeout='5s'");
  if(action==='schema'){await client.query(await readFile(new URL('../deploy-next/migrations/007-merchant-search-index.sql',import.meta.url),'utf8'));await client.query(await readFile(new URL('../deploy-next/migrations/008-merchant-search-telemetry.sql',import.meta.url),'utf8'));await client.query('COMMIT');console.log(JSON.stringify({database,schemaApplied:true,sourceRowsChanged:false}));}
  else{const result=await buildMerchantSearchIndex(client);await client.query('COMMIT');console.log(JSON.stringify({database,...result,sourceRowsChanged:false}));}
 }
}catch(error){await client.query('ROLLBACK').catch(()=>{});throw error;}finally{await client.end();}
