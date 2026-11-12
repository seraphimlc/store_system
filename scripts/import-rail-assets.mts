/** Railway import CLI: preview by default; writes only an explicitly isolated local acceptance DB. */
import '../apps/server/node_modules/reflect-metadata/Reflect.js';import {readFile,mkdir,writeFile} from 'node:fs/promises';import {Database} from '../apps/server/src/database';import {RailImportService} from '../apps/server/src/rail-import';import {railReference} from '../packages/domain/src/rail-reference';
const args=process.argv.slice(2),flags=new Set(['--apply','--baseline','--no-tasks']),values=new Set(['--json','--token','--actor']),options:Record<string,string|boolean>={};
for(let i=0;i<args.length;i++){if(flags.has(args[i]))options[args[i]]=true;else if(values.has(args[i])&&args[i+1]&&!args[i+1].startsWith('--'))options[args[i]]=args[++i];else throw Error('Expected --json/--token/--actor value or --apply/--baseline/--no-tasks');}
const apply=!!options['--apply'],url=new URL(process.env.NEXT_DATABASE_URL??'');if(!['postgres:','postgresql:'].includes(url.protocol))throw Error('Explicit PostgreSQL connection required');
if(options['--baseline']){if(apply||!['127.0.0.1','localhost'].includes(url.hostname))throw Error('Baseline mode is local and read-only');url.pathname='/store_settle';}
if(apply&&(!['127.0.0.1','localhost'].includes(url.hostname)||!url.pathname.endsWith('_refactor_p1')||typeof options['--token']!=='string'))throw Error('Apply requires an explicit local _refactor_p1 database and --token from preview');
if(!apply)url.searchParams.set('options','-c default_transaction_read_only=on');
const db=new Database({databaseUrl:url.href,sessionSecret:'rail-cli-not-a-login-session-secret-at-least40',cookieSecure:false,port:3000,host:'127.0.0.1'});
try{
 if(!apply&&(await db.query("SELECT current_setting('default_transaction_read_only') AS value")).rows[0].value!=='on')throw Error('Read-only configuration not enforced');
 const actor=options['--actor']?Number(options['--actor']):(await db.query("SELECT id FROM users WHERE role='admin' AND is_active=true AND status IN('active','leave') ORDER BY id LIMIT 1")).rows[0]?.id;if(!Number.isInteger(actor)||actor<=0)throw Error('Explicit valid administrator required');
 const payload=typeof options['--json']==='string'?JSON.parse(await readFile(options['--json'],'utf8')):railReference,service=new RailImportService(db),makeTasks=!options['--no-tasks'];
 const result=apply?await service.apply(actor,{token:options['--token'],makeTasks,confirm:'IMPORT_RAIL_ASSETS'},payload):await service.preview(actor,payload,makeTasks);
 const report={capturedAt:new Date().toISOString(),database:url.pathname.slice(1),mode:apply?'apply':'read-only-preview',makeTasks,result};await mkdir('data/refactor',{recursive:true});await writeFile('data/refactor/rail-import-cli.json',JSON.stringify(report,null,2)+'\n',{mode:0o600});console.log(JSON.stringify(report,null,2));
}finally{await db.onModuleDestroy();}
