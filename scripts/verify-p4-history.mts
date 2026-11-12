import {spawnLegacyPython} from './legacy-python.mjs';
/** Full local snapshot parity. No schema changes or writes through application connections. */
import '../apps/server/node_modules/reflect-metadata/Reflect.js';
import {spawnSync} from 'node:child_process';import {randomUUID,createHash} from 'node:crypto';import {mkdir,writeFile,unlink} from 'node:fs/promises';import {openSync,closeSync} from 'node:fs';import {resolve} from 'node:path';
import {Database} from '../apps/server/src/database';import {PayrollService} from '../apps/server/src/payroll';
import {baselineSql,summarize} from './refactor-baseline.mjs';
const container='store-system-local-db-1',source='store_settle',user='store_settle';
const suffix=new Date().toISOString().replace(/\D/g,'').slice(0,14)+'_'+randomUUID().slice(0,8),target='store_settle_refactor_p4_history_'+suffix;
const directory=resolve('data/refactor'),dump=resolve(directory,'history-'+suffix+'.dump'),output=resolve(directory,'p4-history.json');let created=false,db:Database|undefined;
function docker(args:string[],input?:string){const result=spawnSync('docker',['exec',...(input?['-i']:[]),container,...args],{input,encoding:'utf8',maxBuffer:16*1024*1024,timeout:300000});if(result.error||result.status!==0)throw Error('LOCAL_SNAPSHOT_COMMAND_FAILED');return result.stdout.trim();}
function sql(database:string,query:string){return docker(['psql','-X','-A','-t','-q','-v','ON_ERROR_STOP=1','-U',user,'-d',database],query);}
function snapshot(database:string){const raw=JSON.parse(sql(database,baselineSql()));return {...summarize(raw),financial:{formal:raw.formal_months,performance:raw.performance_months,payments:raw.payments,adjusts:raw.adjusts}};}
function digest(value:unknown){return createHash('sha256').update(JSON.stringify(value)).digest('hex');}
const report:{[key:string]:unknown}={capturedAt:new Date().toISOString(),source,target,scope:'Full local snapshot; read-only payroll parity, no settlement rebuild or new payment registration',months:[]};
try{
 const connection=new URL(process.env.NEXT_TEST_DATABASE_URL??'');if(!['127.0.0.1','localhost'].includes(connection.hostname)||!connection.pathname.endsWith('_refactor_p1'))throw Error('EXPLICIT_LOCAL_TEST_CONNECTION_REQUIRED');
 await mkdir(directory,{recursive:true});const before=snapshot(source);report.sourceBefore=before;
 // Atomic database creation fails if a target already exists; only our freshly-created target is ever dropped.
 sql('postgres',`CREATE DATABASE "${target}";`);created=true;
 const fd=openSync(dump,'wx',0o600);try{const result=spawnSync('docker',['exec',container,'pg_dump','-U',user,'-d',source,'-Fc','--no-owner','--no-privileges'],{stdio:['ignore',fd,'pipe'],timeout:300000});if(result.error||result.status!==0)throw Error('LOCAL_DUMP_FAILED');}finally{closeSync(fd);}
 const input=openSync(dump,'r');try{const result=spawnSync('docker',['exec','-i',container,'pg_restore','-U',user,'-d',target,'--no-owner','--no-privileges','--exit-on-error'],{stdio:[input,'ignore','pipe'],timeout:300000});if(result.error||result.status!==0)throw Error('LOCAL_RESTORE_FAILED');}finally{closeSync(input);}
 const cloned=snapshot(target);report.clone=cloned;if(before.schemaSha256!==cloned.schemaSha256)throw Error('CLONE_SCHEMA_DIFFERS');report.cloneDataBaselineMatches=digest(before.financial)===digest(cloned.financial);if(!report.cloneDataBaselineMatches)throw Error('CLONE_FINANCIAL_BASELINE_DIFFERS');
 connection.pathname='/'+target;connection.searchParams.set('options','-c default_transaction_read_only=on');
 db=new Database({databaseUrl:connection.href,sessionSecret:'history-verification-not-a-login-session-secret',cookieSecure:false,port:3000,host:'127.0.0.1'});
 if((await db.query("SELECT current_setting('default_transaction_read_only') AS mode")).rows[0].mode!=='on')throw Error('READ_ONLY_NOT_ENFORCED');
 const legacy=spawnLegacyPython(['legacy/scripts/refactor-payroll-oracle.py'],{encoding:'utf8',maxBuffer:16*1024*1024,timeout:120000,env:{...process.env,PYTHONPATH:process.cwd(),DATABASE_URL:connection.href.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:'),AI_API_KEY:'',LOGIN_NAME_AI:'0',RECON_LAYOUT_AI:'0'}});if(legacy.error||legacy.status!==0)throw Error('LEGACY_READ_ONLY_ORACLE_FAILED');const expected=JSON.parse(legacy.stdout);
 const service=new PayrollService(db),months=await service.months(),checks:unknown[]=[];let mismatches=0;
 for(const month of months){
  const actual=await service.read(month),old=expected[month];if(!old)throw Error('LEGACY_MONTH_MISSING');
  const normalized=actual.rows.map(row=>({code:row.code,half1:[row.half1.adjustment,row.half1.payable],half2:[row.half2.adjustment,row.half2.payable],gross1:row.half1.amount,gross2:row.half2.amount,points1:row.half1.points,points2:row.half2.points,settle:row.settlePoints,settleAmount:row.settleAmount,diff:row.diffPoints,diffAmount:row.diffAmount}));
  const oldMap=new Map<string,Record<string,unknown>>(old.rows.map((row:{code:string})=>[row.code,row])),fieldDiffs:Record<string,number>={};
  for(const row of normalized){const previous=oldMap.get(row.code);if(!previous){fieldDiffs.missingLegacy=(fieldDiffs.missingLegacy??0)+1;continue;}for(const [field,value]of Object.entries(row))if(JSON.stringify(value)!==JSON.stringify(previous[field]))fieldDiffs[field]=(fieldDiffs[field]??0)+1;oldMap.delete(row.code);}if(oldMap.size)fieldDiffs.missingNew=oldMap.size;
  const carry=Object.fromEntries(actual.carry.map(row=>[row.code,[row.points,row.amount]]));const allCodes=new Set([...Object.keys(carry),...Object.keys(old.carry)]);for(const code of allCodes)if(JSON.stringify(carry[code])!==JSON.stringify(old.carry[code]))fieldDiffs.carry=(fieldDiffs.carry??0)+1;
  const direct=(await db.query(`SELECT (SELECT count(*)::int FROM payroll_period_rows WHERE month=$1) AS people,(SELECT coalesce(sum(amount),0)::text FROM payroll_payments WHERE month=$1) AS gross,(SELECT count(*)::int FROM payroll_payments WHERE month=$1) AS "paymentRows",(SELECT count(*)::int FROM month_perf_records WHERE month=$1) AS "perfRows",(SELECT count(*)::int FROM formal_records WHERE to_char(japan_date,'YYYY-MM')=$1) AS "formalRows"`,[month])).rows[0];
  if(actual.summary.people!==direct.people)fieldDiffs.sqlPeople=1;if(actual.summary.registeredAmount!==Number(direct.gross))fieldDiffs.sqlGross=1;if(actual.performance.length!==direct.perfRows)fieldDiffs.sqlPerformance=1;
  mismatches+=Object.values(fieldDiffs).reduce((sum,value)=>sum+value,0);checks.push({month,people:actual.rows.length,performanceRows:actual.performance.length,formalRows:direct.formalRows,paymentRows:direct.paymentRows,carryPeople:actual.carry.length,half1Payable:actual.summary.half1Payable,half2Payable:actual.summary.half2Payable,registeredGrossSnapshot:actual.summary.registeredAmount,fieldDiffs});
 }
 report.months=checks;report.mismatches=mismatches;report.readOnly=true;report.cloneUnchanged=digest(cloned)===digest(snapshot(target));const after=snapshot(source);report.sourceAfter=after;report.sourceBaselineUnchanged=digest(before)===digest(after);report.ok=mismatches===0&&report.cloneUnchanged&&report.sourceBaselineUnchanged;
 if(!report.ok)process.exitCode=1;
}catch(error){report.ok=false;report.error=error instanceof Error&&/^[A-Z_]+$/.test(error.message)?error.message:'HISTORY_VERIFICATION_FAILED';process.exitCode=1;}
finally{
 await db?.onModuleDestroy();if(created){try{sql('postgres',`DROP DATABASE "${target}";`);report.cloneRemoved=true;}catch{report.cloneRemoved=false;report.ok=false;process.exitCode=1;}}
 await unlink(dump).catch(()=>{});await mkdir(directory,{recursive:true});await writeFile(output,JSON.stringify(report,null,2)+'\n',{mode:0o600});console.log(JSON.stringify({ok:report.ok,error:report.error,months:report.months,mismatches:report.mismatches,cloneRemoved:report.cloneRemoved,sourceBaselineUnchanged:report.sourceBaselineUnchanged,output},null,2));
}
