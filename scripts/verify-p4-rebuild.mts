import {spawnLegacyPython} from './legacy-python.mjs';
/** Same-snapshot full-data rebuild comparison. Never outputs source rows or identities. */
import '../apps/server/node_modules/reflect-metadata/Reflect.js';import {spawnSync} from 'node:child_process';import {randomUUID,createHash} from 'node:crypto';import {mkdir,writeFile,unlink} from 'node:fs/promises';import {openSync,closeSync} from 'node:fs';import {resolve} from 'node:path';
import {Database} from '../apps/server/src/database';import {SettlementService} from '../apps/server/src/settlement';import {SettlementSyncService} from '../apps/server/src/settlement-sync';import {ReportComparisonService} from '../apps/server/src/report-comparison';import {baselineSql} from './refactor-baseline.mjs';
const container='store-system-local-db-1',user='store_settle',source='store_settle',token=new Date().toISOString().replace(/\D/g,'').slice(0,14)+'_'+randomUUID().slice(0,8),prefix='store_settle_refactor_p4_rebuild_',targets=[prefix+token+'_old',prefix+token+'_new'],created:string[]=[],directory=resolve('data/refactor'),dump=resolve(directory,'rebuild-'+token+'.dump');let db:Database|undefined;
const report:any={capturedAt:new Date().toISOString(),scope:'Two full local clones; monthly rebuild parity, no source raw output',months:[],ok:false};
function docker(args:string[],input?:string){const r=spawnSync('docker',['exec',...(input?['-i']:[]),container,...args],{input,encoding:'utf8',maxBuffer:16*1024*1024,timeout:300000});if(r.error||r.status!==0)throw Error('LOCAL_DATABASE_COMMAND_FAILED');return r.stdout.trim();}
function sql(database:string,query:string){return docker(['psql','-X','-A','-t','-q','-v','ON_ERROR_STOP=1','-U',user,'-d',database],query);}
function hash(value:unknown){return createHash('sha256').update(JSON.stringify(value)).digest('hex');}
function baseline(database:string){return JSON.parse(sql(database,baselineSql()));}
function digest(database:string,table:string,expression:string,where='true'){
 return JSON.parse(sql(database,`BEGIN READ ONLY; SELECT json_build_object('count',count(*),'digest',md5(coalesce(string_agg(h,'' ORDER BY h),''))) FROM (SELECT md5((${expression})::text) AS h FROM ${table} t WHERE ${where}) s; ROLLBACK;`));
}
function evidence(database:string){return {
 raw:digest(database,'raw_records',"jsonb_build_array(t.id,t.clean_status,t.confirm_state,t.filtered_by_raw_id)"),
 formal:digest(database,'formal_records',"to_jsonb(t)-'id'-'created_at'"),
 daily:digest(database,'person_daily_stats',"to_jsonb(t)-'id'"),
 monthly:digest(database,'month_perf_records',"to_jsonb(t)-'id'-'created_at'"),
 periods:digest(database,'payroll_period_rows',"to_jsonb(t)-'id'-'updated_at'-'updated_by'"),
 debts:digest(database,'payroll_adjusts',"to_jsonb(t)-'id'-'updated_at'-'settled_at'"),
 payments:digest(database,'payroll_payments','to_jsonb(t)'),links:digest(database,'payroll_settlement_links','to_jsonb(t)'),appeals:digest(database,'appeals','to_jsonb(t)')
};}
function fieldDifferences(oldDatabase:string,newDatabase:string,table:string){
 const read=(database:string)=>JSON.parse(sql(database,`BEGIN READ ONLY; SELECT coalesce(json_agg(jsonb_build_object('key',md5(month||chr(1)||person_code),'value',to_jsonb(t)-'id'-'person_code'-'created_at'-'updated_at'-'updated_by') ORDER BY month,person_code),'[]') FROM ${table} t; ROLLBACK;`));
 const oldRows=new Map(read(oldDatabase).map((row:any)=>[row.key,row.value])),newRows=read(newDatabase),fields:Record<string,{count:number;examples:unknown[]}>={};
 for(const row of newRows){const previous:any=oldRows.get(row.key);if(!previous)continue;for(const [field,value]of Object.entries(row.value)){if(JSON.stringify(previous[field])===JSON.stringify(value))continue;const entry=fields[field]??={count:0,examples:[]};entry.count++;const example={old:previous[field],next:value};if(entry.examples.length<3&&!entry.examples.some(item=>JSON.stringify(item)===JSON.stringify(example)))entry.examples.push(example);}}
 return fields;
}
function rawFieldDigests(database:string){return JSON.parse(sql(database,`BEGIN READ ONLY; SELECT json_agg(x ORDER BY month) FROM(SELECT left(modified_raw,7) AS month,count(*) AS count,md5(string_agg(md5(jsonb_build_array(id,clean_status)::text),'' ORDER BY id)) AS status,md5(string_agg(md5(jsonb_build_array(id,confirm_state)::text),'' ORDER BY id)) AS confirmation,md5(string_agg(md5(jsonb_build_array(id,filtered_by_raw_id)::text),'' ORDER BY id)) AS reference FROM raw_records GROUP BY 1) x; ROLLBACK;`));}
function carrySnapshotCheck(database:string,month:string){if(!/^[0-9]{4}-[0-9]{2}$/.test(month))throw Error('INVALID_MONTH');return JSON.parse(sql(database,`BEGIN READ ONLY; WITH debt AS(SELECT p.month,a.person_code,sum(a.remaining) AS amount FROM (SELECT DISTINCT month FROM payroll_period_rows)p JOIN payroll_adjusts a ON a.source_month<p.month AND a.status='in_progress' GROUP BY p.month,a.person_code), paid AS(SELECT month,half FROM payroll_paid_marks) SELECT json_build_object('mismatches',count(*) FILTER(WHERE r.prev_adjust_amount<>r.diff_amount+least(0,coalesce(d.amount,0)+CASE WHEN EXISTS(SELECT 1 FROM paid WHERE month=r.month AND half=1) THEN 0 ELSE r.half1_amount END+CASE WHEN EXISTS(SELECT 1 FROM paid WHERE month=r.month AND half=2) THEN 0 ELSE r.half2_amount END))) FROM payroll_period_rows r LEFT JOIN debt d USING(month,person_code) WHERE r.month<='${month}'; ROLLBACK;`));}
async function save(){await writeFile(resolve(directory,'p4-rebuild.json'),JSON.stringify(report,null,2)+'\n',{mode:0o600});}
try{
 const url=new URL(process.env.NEXT_TEST_DATABASE_URL??'');if(!['127.0.0.1','localhost'].includes(url.hostname)||url.pathname!=='/store_settle')throw Error('EXPLICIT_LOCAL_CONNECTION_REQUIRED');await mkdir(directory,{recursive:true});
 const before=baseline(source);report.sourceBeforeHash=hash(before);const fd=openSync(dump,'wx',0o600);try{const r=spawnSync('docker',['exec',container,'pg_dump','-U',user,'-d',source,'-Fc','--no-owner','--no-privileges'],{stdio:['ignore',fd,'pipe'],timeout:300000});if(r.error||r.status!==0)throw Error('DUMP_FAILED');}finally{closeSync(fd);}
 for(const target of targets){sql('postgres',`CREATE DATABASE "${target}";`);created.push(target);const input=openSync(dump,'r');try{const r=spawnSync('docker',['exec','-i',container,'pg_restore','-U',user,'-d',target,'--no-owner','--no-privileges','--exit-on-error'],{stdio:[input,'ignore','pipe'],timeout:300000});if(r.error||r.status!==0)throw Error('RESTORE_FAILED');}finally{closeSync(input);}}
 const initial=evidence(targets[0]);report.clonesInitiallyEqual=hash(initial)===hash(evidence(targets[1]));if(!report.clonesInitiallyEqual)throw Error('CLONE_DATA_DIFFERS');
 const months=JSON.parse(sql(targets[1],"BEGIN READ ONLY; SELECT json_agg(m ORDER BY m) FROM(SELECT DISTINCT month AS m FROM month_perf_records UNION SELECT DISTINCT month FROM payroll_period_rows) q; ROLLBACK;"));
 url.pathname='/'+targets[1];db=new Database({databaseUrl:url.href,sessionSecret:'rebuild-development-isolation-secret-at-least40',cookieSecure:false,port:3000,host:'127.0.0.1'});const service=new SettlementService(db,new SettlementSyncService(db,new ReportComparisonService(db)));
 const sealed=(await db.query<{month:string}>('SELECT month FROM sealed_months ORDER BY month')).rows.map(row=>row.month);report.sealedGuards=[];
 for(const month of sealed.filter(value=>months.includes(value))){const protectedBefore=evidence(targets[1]);let code:string|undefined;try{await service.rebuild(month);}catch(error){code=(error as {code?:string}).code;}const unchanged=hash(protectedBefore)===hash(evidence(targets[1]));report.sealedGuards.push({month,code,unchanged});if(code!=='MONTH_SEALED'||!unchanged)throw Error('SEALED_GUARD_FAILED');}
 // Algorithm replay is explicitly unsealed in disposable clones only. Source seals remain untouched.
 for(const target of targets)sql(target,'DELETE FROM sealed_months;');report.replayUnsealedMonths=sealed;
 const env={...process.env,AI_API_KEY:'',VISIT_REPORT_AI:'0',AI_AUTO_INTERPRET:'0',AI_RECON_INTERPRET:'0',LOGIN_NAME_AI:'0',STORE_AI_BATCH:'0'};
 for(const month of months){
  console.log(JSON.stringify({event:'rebuild_month_start',month}));const rawBefore=rawFieldDigests(targets[1]);const oldUrl=new URL(url);oldUrl.pathname='/'+targets[0];const started=Date.now();
  const r=spawnLegacyPython(['legacy/scripts/refactor-rebuild-oracle.py',month],{encoding:'utf8',maxBuffer:1024*1024,timeout:240000,env:{...env,PYTHONPATH:process.cwd(),DATABASE_URL:oldUrl.href.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:')}});if(r.error||r.status!==0)throw Error('LEGACY_REBUILD_FAILED');const old=JSON.parse(r.stdout);let current:any;try{current=await service.rebuild(month);}catch(error){current={ok:false,code:(error as {code?:string}).code??'REBUILD_FAILED'};}
  const a=evidence(targets[0]),b=evidence(targets[1]),differences=Object.keys(a).filter(key=>JSON.stringify(a[key])!==JSON.stringify(b[key]));
  const result={month,legacy:old,next:{ok:current.ok,code:current.code,formalAfter:current.formalAfter,pointsAfter:current.pointsAfter},differentTables:differences,counts:Object.fromEntries(Object.keys(a).map(key=>[key,{old:a[key].count,next:b[key].count}])),paymentsPreserved:hash(b.payments)===hash(initial.payments),linksPreserved:hash(b.links)===hash(initial.links),appealsPreserved:hash(b.appeals)===hash(initial.appeals),elapsedMs:Date.now()-started};const rawOld=rawFieldDigests(targets[0]),rawNew=rawFieldDigests(targets[1]),targetRawEqual=JSON.stringify(rawOld.find((row:any)=>row.month===month))===JSON.stringify(rawNew.find((row:any)=>row.month===month)),otherMonthsUnchanged=rawBefore.filter((row:any)=>row.month!==month).every((row:any)=>JSON.stringify(row)===JSON.stringify(rawNew.find((next:any)=>next.month===row.month)));
  const fields=Object.fromEntries(differences.filter(key=>['monthly','periods'].includes(key)).map(key=>[key,fieldDifferences(targets[0],targets[1],key==='monthly'?'month_perf_records':'payroll_period_rows')]));
  const carryChecks={old:carrySnapshotCheck(targets[0],month),next:carrySnapshotCheck(targets[1],month)},documentedDifferences:string[]=[],unexpected=differences.filter(key=>{
   if(key==='raw'&&targetRawEqual&&otherMonthsUnchanged){documentedDifferences.push('legacy-rejudges-other-months');return false;}
   if(key==='periods'&&Object.keys(fields.periods??{}).length===1&&fields.periods.prev_adjust_amount&&carryChecks.next.mismatches===0){documentedDifferences.push('legacy-stale-carry-snapshot');return false;}
   return true;
  });
  report.months.push({...result,documentedDifferences,unexpectedTables:unexpected,targetRawEqual,otherMonthsUnchanged,rawFieldDigests:differences.includes('raw')?{old:rawOld,next:rawNew}:undefined,carrySnapshotCheck:carryChecks,fieldDifferences:fields});await save();console.log(JSON.stringify({...result,documentedDifferences,unexpectedTables:unexpected,targetRawEqual,otherMonthsUnchanged,carrySnapshotCheck:carryChecks}));
  // Stop on divergence before later mutations can obscure the first cause.
  if(unexpected.length||!targetRawEqual||!otherMonthsUnchanged||old.ok!==current.ok||!result.paymentsPreserved||!result.linksPreserved||!result.appealsPreserved)throw Error('REBUILD_DIVERGENCE');
 }
 report.sourceUnchanged=hash(before)===hash(baseline(source));report.ok=report.sourceUnchanged;
}catch(error){report.error=error instanceof Error&&/^[A-Z_]+$/.test(error.message)?error.message:'REBUILD_VERIFICATION_FAILED';report.ok=false;process.exitCode=1;}
finally{await db?.onModuleDestroy();if(report.sourceBeforeHash){try{report.sourceUnchanged=report.sourceBeforeHash===hash(baseline(source));if(!report.sourceUnchanged){report.ok=false;process.exitCode=1;}}catch{report.sourceUnchanged=false;report.ok=false;process.exitCode=1;}}report.clonesRemoved=true;for(const target of created)try{sql('postgres',`DROP DATABASE "${target}";`);}catch{report.clonesRemoved=false;report.ok=false;process.exitCode=1;}await unlink(dump).catch(()=>{});await mkdir(directory,{recursive:true});await save();console.log(JSON.stringify({ok:report.ok,error:report.error,clonesRemoved:report.clonesRemoved,output:'data/refactor/p4-rebuild.json'}));}
