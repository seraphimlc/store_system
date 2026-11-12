import { mkdir, open, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { DurableQueue } from './queue';
import { hostname } from 'node:os';
import { resolve } from 'node:path';
async function main(){
 const directory=process.env.NEXT_TASK_DIRECTORY ?? '/data/jobs';
 await mkdir(directory,{recursive:true,mode:0o700});
 const lock=join(directory,'worker.lock');
 try { const file=await open(lock,'wx',0o600); await file.writeFile(JSON.stringify({pid:process.pid,hostname:hostname()}));await file.close(); }
 catch {
   const previous=JSON.parse(await readFile(lock,'utf8')) as {pid:number;hostname:string};
   if(previous.hostname===hostname() && previous.pid!==process.pid){
     try { process.kill(previous.pid,0); throw new Error('Another worker is active'); }
     catch(error){if((error as NodeJS.ErrnoException).code!=='ESRCH')throw error;}
   }
   // All releases use the same persistent volume. Deployment drains and stops the old worker first.
   await rename(lock,lock+'.stale');await unlink(lock+'.stale');
   const file=await open(lock,'wx',0o600);await file.writeFile(JSON.stringify({pid:process.pid,hostname:hostname()}));await file.close();
 }
 const queue=new DurableQueue(directory);
 const supported=new Set(['system.check','report.analysis','dashboard.analysis','settlement.judge','settlement.finalize','settlement.rebuild','import.visit','import.delete','reconciliation.monthly','store.master','reconciliation.insight','store.ai']);
 await queue.recover(supported);
 const sourceMode=process.env.NEXT_WORKER_SOURCE==='true';
 const runtimePath=(name:string)=>resolve(process.cwd(),sourceMode?'apps/server/src/'+name+'.ts':'apps/server/dist/apps/server/src/'+name+'.js');
 const reconcile=process.env.NEXT_DATABASE_URL&&process.env.NEXT_WORKER_RECONCILE!=='false';
 const reportRuntime=()=>require(runtimePath('report-analysis')) as {runReportTask:(payload:unknown)=>Promise<unknown>;reconcileReportTasks:()=>Promise<void>};
 const dashboardRuntime=()=>require(runtimePath('dashboard')) as {runDashboardTask:(payload:unknown)=>Promise<unknown>;reconcileDashboardTasks:()=>Promise<void>};
 const settlementRuntime=()=>require(runtimePath('settlement-jobs')) as {runSettlementTask:(payload:unknown)=>Promise<unknown>};
 const importRuntime=()=>require(runtimePath('visit-imports')) as {runVisitImportTask:(payload:unknown)=>Promise<unknown>;reconcileVisitImports:()=>Promise<void>};
 const reconRuntime=()=>require(runtimePath('reconciliation')) as {runReconciliationTask:(payload:unknown)=>Promise<unknown>;reconcileReconciliationTasks:()=>Promise<void>};
 const deletionRuntime=()=>require(runtimePath('import-deletion')) as {runImportDeletionTask:(payload:unknown)=>Promise<unknown>};
 const storeRuntime=()=>require(runtimePath('store-master')) as {runStoreMasterTask:(value:unknown)=>Promise<unknown>};
 const reconInsightRuntime=()=>require(runtimePath('reconciliation-insight')) as {runReconciliationInsightTask:(payload:unknown)=>Promise<unknown>;reconcileReconciliationInsights:()=>Promise<void>};
 const storeAiRuntime=()=>require(runtimePath('store-ai')) as {runStoreAiTask:(payload:unknown)=>Promise<unknown>;reconcileStoreAiTasks:()=>Promise<void>};
 if(reconcile){await reportRuntime().reconcileReportTasks();await dashboardRuntime().reconcileDashboardTasks();await importRuntime().reconcileVisitImports();await reconRuntime().reconcileReconciliationTasks();await reconInsightRuntime().reconcileReconciliationInsights();await storeAiRuntime().reconcileStoreAiTasks();}
 let stopped=false;
 for(const signal of ['SIGTERM','SIGINT'] as const)process.on(signal,()=>{stopped=true;});
 const heartbeat=setInterval(()=>{writeFile(join(directory,'heartbeat'),String(Date.now()),{mode:0o600}).catch(()=>{stopped=true;});},2000);
 heartbeat.unref();
 try{
  let reconciledAt=Date.now();
  while(!stopped){
   if(reconcile&&Date.now()-reconciledAt>30000){await reportRuntime().reconcileReportTasks();await dashboardRuntime().reconcileDashboardTasks();await importRuntime().reconcileVisitImports();await reconRuntime().reconcileReconciliationTasks();await reconInsightRuntime().reconcileReconciliationInsights();await storeAiRuntime().reconcileStoreAiTasks();reconciledAt=Date.now();}
   await writeFile(join(directory,'heartbeat'),String(Date.now()),{mode:0o600});
   const task=await queue.claim();
   if(!task){await new Promise(r=>setTimeout(r,500));continue;}
   try{
    if(!supported.has(task.kind))throw new Error('Unsupported task type');
    const result=task.kind==='report.analysis'?await reportRuntime().runReportTask(task.payload):task.kind==='dashboard.analysis'?await dashboardRuntime().runDashboardTask(task.payload):task.kind.startsWith('settlement.')?await settlementRuntime().runSettlementTask(task.payload):task.kind==='import.visit'?await importRuntime().runVisitImportTask(task.payload):task.kind==='import.delete'?await deletionRuntime().runImportDeletionTask(task.payload):task.kind==='reconciliation.monthly'?await reconRuntime().runReconciliationTask(task.payload):task.kind==='store.master'?await storeRuntime().runStoreMasterTask(task.payload):task.kind==='reconciliation.insight'?await reconInsightRuntime().runReconciliationInsightTask(task.payload):task.kind==='store.ai'?await storeAiRuntime().runStoreAiTask(task.payload):{ok:true};
    await queue.finish(task,result,(task.kind==='dashboard.analysis'||task.kind==='import.visit'||task.kind==='import.delete'||task.kind==='reconciliation.monthly'||task.kind==='store.master'||task.kind==='reconciliation.insight'||task.kind==='store.ai'||task.kind.startsWith('settlement.'))&&!!(result as {code?:string}).code);
   }catch{if(['report.analysis','dashboard.analysis','settlement.judge','settlement.finalize','settlement.rebuild','import.visit','import.delete','reconciliation.monthly','store.master','reconciliation.insight','store.ai'].includes(task.kind))throw new Error('REPORT_TASK_INTERRUPTED');await queue.finish(task,{code:'TASK_FAILED'},true);}
  }
 }finally{clearInterval(heartbeat);await unlink(lock);}
}
main().catch(()=>{console.error(JSON.stringify({event:'worker_startup_failed'}));process.exitCode=1;});
