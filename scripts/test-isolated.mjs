/** Schema-only disposable PostgreSQL database for existing destructive Node tests. Never changes application configuration. */
import {spawn,spawnSync} from 'node:child_process';import {createRequire} from 'node:module';import {readdir,readFile,mkdir,writeFile} from 'node:fs/promises';import {resolve} from 'node:path';
import {sourceConnection,temporaryDatabaseName,dropStatement,testEnvironment,selectTestFiles,testNameArguments} from './test-isolation-lib.mjs';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url)),{Pool}=require('pg');
let child,admin,created=false,interrupted=false,source,name;const signals=[];
function cli(args,input){const result=spawnSync('docker',args,{encoding:'utf8',input,maxBuffer:16*1024*1024,timeout:60000});if(result.error||result.status!==0)throw Error('Local PostgreSQL schema operation failed; no source data was changed');return result.stdout.trim();}
async function tests(integration){const directories=integration?['apps/server/test']:['apps/server/test','apps/worker/test','packages/domain/test','scripts'];const files=[];for(const dir of directories)for(const name of (await readdir(dir)).sort())if(name.endsWith(dir==='scripts'?'.test.mjs':'.test.ts'))files.push(dir+'/'+name);return selectTestFiles(files,process.argv.slice(2));}
async function fingerprint(pool){return (await pool.query(`SELECT current_database() AS database,current_setting('transaction_read_only') AS read_only,
 (SELECT count(*)::int FROM users) AS users,(SELECT count(*)::int FROM formal_records) AS formal_records,
 (SELECT count(*)::int FROM month_perf_records) AS month_perf_records,(SELECT count(*)::int FROM bd_main_task) AS batches,
 (SELECT md5(coalesce(string_agg(id::text||':'||password_hash,',' ORDER BY id),'')) FROM users) AS password_digest`)).rows[0];}
async function main(){
 const args=process.argv.slice(2),integration=args.includes('--integration'),browser=args.includes('--browser');if(args.some(arg=>!['--integration','--browser','--docker-mcp','--ops','--maps','--management','--h5','--map-workspace','--messages','--h5-grid','--oauth'].includes(arg)&&!arg.startsWith('--file=')&&!arg.startsWith('--name='))||(integration&&browser)||((args.includes('--oauth')||args.includes('--ops')||args.includes('--maps')||args.includes('--management')||args.includes('--h5')||args.includes('--map-workspace')||args.includes('--messages')||args.includes('--h5-grid'))&&!browser)||(args.filter(value=>['--ops','--maps','--management','--h5','--map-workspace','--messages','--h5-grid','--oauth'].includes(value)).length>1))throw Error('Use --integration or --browser [--ops|--maps|--management|--h5|--map-workspace|--messages|--h5-grid]');
 testNameArguments(args);
 if(args.includes('--docker-mcp')&&(integration||browser||args.some(arg=>arg.startsWith('--file=')||arg.startsWith('--name='))))throw Error('--docker-mcp is an independent isolated acceptance mode');
 if(args.some(arg=>arg.startsWith('--file=')))await tests(integration);
 if(!process.env.NEXT_TEST_DATABASE_URL){try{process.loadEnvFile(resolve('.env.next.local'));}catch(error){if(error.code!=='ENOENT')throw Error('Unable to load local test schema source');}}
 if(!process.env.NEXT_TEST_DATABASE_URL){if(integration||browser)throw Error('A local PostgreSQL schema source is required');console.log('[tests] No PostgreSQL source configured; running existing non-integration tests');return runTests(await tests(false),process.env);}
 source=sourceConnection(process.env.NEXT_TEST_DATABASE_URL);name=temporaryDatabaseName();
 const container=process.env.NEXT_TEST_PG_CONTAINER??'store-system-local-db-1';if(!/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(container))throw Error('Invalid local PostgreSQL container name');
 const readUrl=new URL(source.url);readUrl.searchParams.set('options','-c default_transaction_read_only=on');const reader=new Pool({connectionString:readUrl.href,max:1,application_name:'store-test-source-readonly'});
 let before,after;
 try{
  const identity=(await reader.query('SELECT system_identifier::text AS id FROM pg_control_system()')).rows[0].id;
  const local=cli(['exec',container,'psql','-XAt','-U',source.user,'-d',source.database,'-c','SELECT system_identifier::text FROM pg_control_system()']);
  if(identity!==local)throw Error('PostgreSQL container does not match the configured local source');
  before=await fingerprint(reader);if(before.read_only!=='on')throw Error('Schema source must be read-only');
  const schema=cli(['exec','-e','PGOPTIONS=-c default_transaction_read_only=on',container,'pg_dump','--schema-only','--no-owner','--no-privileges','-U',source.user,'-d',source.database]);
  if(interrupted)throw Error('Test setup interrupted');
  const adminUrl=new URL(source.url);adminUrl.pathname='/postgres';admin=new Pool({connectionString:adminUrl.href,max:1,application_name:'store-disposable-test-admin'});
  await admin.query(`CREATE DATABASE "${name}" TEMPLATE template0`);created=true;
  cli(['exec','-i',container,'psql','-Xq','-v','ON_ERROR_STOP=1','-U',source.user,'-d',name],schema);
  cli(['exec','-i',container,'psql','-Xq','-v','ON_ERROR_STOP=1','-U',source.user,'-d',name],await readFile('deploy-next/migrations/001-auth-security.sql','utf8'));
  cli(['exec','-i',container,'psql','-Xq','-v','ON_ERROR_STOP=1','-U',source.user,'-d',name],await readFile('deploy-next/migrations/002-store-search-log.sql','utf8'));
  if(interrupted)throw Error('Test setup interrupted');
  const target=new URL(source.url);target.pathname='/'+name;
  const probe=new Pool({connectionString:target.href,max:1});try{const row=(await probe.query('SELECT (SELECT count(*)::int FROM users) AS users,(SELECT count(*)::int FROM formal_records) AS records')).rows[0];if(row.users||row.records)throw Error('Disposable test database must have schema only');}finally{await probe.end();}
  console.log('[tests] Running against a disposable schema-only database; baseline connection is unchanged');
  const code=args.includes('--docker-mcp')?await runDocker(testEnvironment(process.env,target.href)):browser?await runBrowser(testEnvironment(process.env,target.href)):await runTests(await tests(integration),testEnvironment(process.env,target.href));
  after=await fingerprint(reader);if(JSON.stringify(before)!==JSON.stringify(after))throw Error('Baseline aggregates or password digest changed during test run; inspect concurrent writers');
  await mkdir('data/refactor',{recursive:true});await writeFile((process.argv.includes('--browser')?'data/refactor/browser-isolation-last.json':'data/refactor/test-isolation-last.json'),JSON.stringify({ok:code===0,source:source.database,sourceReadOnly:true,schemaOnly:true,...(browser?{browserSuite:args.includes('--oauth')?'oauth':args.includes('--h5-grid')?'h5-grid':args.includes('--messages')?'messages':args.includes('--map-workspace')?'map-workspace':args.includes('--h5')?'h5':args.includes('--management')?'management':args.includes('--ops')?'ops':args.includes('--maps')?'maps':'all'}:{}),temporaryDatabase:name,sourceBefore:before,sourceAfter:after,baselineUnchanged:true,testExit:code,cleanup:'pending'},null,2));
  return code;
 }finally{await reader.end();}
}
function runDocker(env){return new Promise((resolve,reject)=>{child=spawn(process.execPath,['scripts/verify-mcp-docker.mjs'],{stdio:'inherit',env});child.once('error',reject);child.once('exit',(code,signal)=>{child=undefined;resolve(signal?130:code??1);});});}
function runBrowser(env){return new Promise((resolve,reject)=>{child=spawn(process.execPath,['--import','tsx','scripts/browser-isolated.mts',process.argv.includes('--oauth')?'oauth':process.argv.includes('--h5-grid')?'h5-grid':process.argv.includes('--messages')?'messages':process.argv.includes('--map-workspace')?'map-workspace':process.argv.includes('--h5')?'h5':process.argv.includes('--management')?'management':process.argv.includes('--ops')?'ops':process.argv.includes('--maps')?'maps':'all'],{stdio:'inherit',env});child.once('error',reject);child.once('exit',(code,signal)=>{child=undefined;resolve(signal?130:code??1);});});}
function runTests(files,env){return new Promise((resolve,reject)=>{child=spawn(process.execPath,['--import','tsx','--test','--test-concurrency=1',...testNameArguments(process.argv.slice(2)),...files],{stdio:'inherit',env});child.once('error',reject);child.once('exit',(code,signal)=>{child=undefined;resolve(signal?130:code??1);});});}
for(const signal of ['SIGINT','SIGTERM']){const handler=()=>{interrupted=true;child?.kill(signal);};process.on(signal,handler);signals.push([signal,handler]);}
let exit=1;
try{exit=await main();}catch{console.error('[tests] Isolated test run failed; source database was not selected as a test target');}
finally{
 let cleaned=false;
 if(created&&admin){try{await admin.query(dropStatement(name,source.database));cleaned=true;console.log('[tests] Removed the database created for this run');}catch{exit=1;console.error('[tests] Temporary database cleanup failed; retained name:',name);}}
 if(cleaned){try{const path=(process.argv.includes('--browser')?'data/refactor/browser-isolation-last.json':'data/refactor/test-isolation-last.json'),record=JSON.parse(await readFile(path,'utf8'));if(record.temporaryDatabase===name){record.cleanup='dropped';await writeFile(path,JSON.stringify(record,null,2));}}catch{}}
 await admin?.end();for(const [signal,handler]of signals)process.off(signal,handler);
}
process.exitCode=interrupted?130:exit;
