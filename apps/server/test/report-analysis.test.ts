import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {spawnSync} from 'node:child_process';
import request from '../node_modules/supertest';import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';import {Database} from '../src/database';import {ReportComparisonService} from '../src/report-comparison';import {ReportAnalysisService} from '../src/report-analysis';import {reportFingerprint,normalizeReport,configuredReportModel} from '../src/report-model';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('AI generation is disabled before any provider request when explicitly switched off',async()=>{const previous=process.env.VISIT_REPORT_AI;process.env.VISIT_REPORT_AI='0';try{await assert.rejects(()=>configuredReportModel('test','zh','test'),/AI_DISABLED/);}finally{if(previous===undefined)delete process.env.VISIT_REPORT_AI;else process.env.VISIT_REPORT_AI=previous;}});
test('report jobs preserve legacy fingerprint, deduplicate, materialize on AI failure, retry and survive partial language completion',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);const directory=await mkdtemp(join(tmpdir(),'store-ai-report-'));const priorDirectory=process.env.NEXT_TASK_DIRECTORY;process.env.NEXT_TASK_DIRECTORY=directory;
 const app=await createApp({databaseUrl:url!,sessionSecret:'analysis-test-at-least-40-characters-secret',cookieSecure:false,port:0,host:'127.0.0.1'});const db=app.get(Database),service=app.get(ReportAnalysisService),comparison=app.get(ReportComparisonService);const code='P3-AI-REPORT',password='report-analysis-fixture';let user:number|undefined;
 const start='2026-01-01',end='2026-01-02';const ids:number[]=[];
 try{
  await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$1,now())',[code]);user=(await db.query(`INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES('analysis-p3-admin',$1,'分析验收管理员','admin','active',true,false,'zh',now()) RETURNING id`,[await hash(password)])).rows[0].id;
  await db.query('INSERT INTO person_daily_stats(person_code,ref_date,records,p1,p2,points) VALUES($1,$2,10,6,4,14),($1,$3,5,5,0,5)',[code,start,end]);
  await db.query(`INSERT INTO staff_daily_reports(person_code,report_date,p1_cnt,p2_cnt,total_cnt,area,submitted_at,created_at) VALUES($1,$2,7,4,11,'fixture',now(),now()),($1,$3,3,0,3,'fixture',now(),now())`,[code,start,end]);
  const original=await comparison.compare(start,end);const script=`from datetime import date\nfrom app.db import SessionLocal\nfrom app.services.report_compare import compare\nfrom app.services.report_ai import data_fingerprint\nimport app.services.report_ai as report_ai\nreport_ai.PROMPT_VERSION=3\ns=SessionLocal()\ntry: print(data_fingerprint(compare(s,date.fromisoformat('${start}'),date.fromisoformat('${end}'))))\nfinally: s.close()`;
  const oracle=spawnLegacyPython(['-c',script],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:')}});assert.equal(oracle.status,0);assert.equal(reportFingerprint(original),oracle.stdout.trim());
  const normalized=normalizeReport({overall_comment:'数字已核对',per_person:{[code]:{comment:'请核实',off_days:[{date:start,delta:-1,note:'已给出的日期'},{date:'1900-01-01',note:'编造'}]},unknown:{comment:'不应显示'}}},original);assert.deepEqual(Object.keys(normalized.per_person),[code]);assert.equal(normalized.per_person[code].off_days.length,1);
  const [one,two]=await Promise.all([service.start(start,end,user!),service.start(start,end,user!)]);assert.equal(one.id,two.id);ids.push(one.id);
  const job=await service.queue.claim();assert.ok(job);assert.equal(await service.queue.claim(),null);
  let calls=0;const processed=await service.process(one.id,(job.payload as {version:string}).version,async(_prompt,language)=>{
   calls++;assert.equal((await db.query("SELECT count(*)::int AS n FROM pg_stat_activity WHERE application_name='store-next-api' AND state='idle in transaction'")).rows[0].n,0);
   if(language==='ja')throw new Error('mock provider failure');return {data:{overall_comment:'已核对',per_person:{[code]:{comment:'请核实'}}},tokens:20,model:'mock'};
  });assert.equal(processed.status,'done');assert.equal(calls,2);await service.queue.finish(job,processed);
  const partial=(await service.list(start,end)).items[0];assert.equal(partial.status,'done');assert.deepEqual(partial.payload.failed_langs,['ja']);assert.equal(partial.ai_tokens,20);
  assert.equal((await service.start(start,end,user!)).id,one.id);assert.equal(await service.queue.claim(),null);
  await assert.rejects(()=>service.retry(one.id),(error:unknown)=>(error as {code:string}).code==='REPORT_NOT_RETRYABLE');
  // Changing daily distribution invalidates both comments and the reusable data fingerprint.
  await db.query('UPDATE staff_daily_reports SET p1_cnt=8,total_cnt=12 WHERE person_code=$1 AND report_date=$2',[code,start]);
  const newer=await service.start(start,end,user!);ids.push(newer.id);assert.notEqual(newer.id,one.id);const failedJob=await service.queue.claim();assert.ok(failedJob);
  const failed=await service.process(newer.id,(failedJob.payload as {version:string}).version,async()=>{throw new Error('mock all languages failed');});await service.queue.finish(failedJob,failed);assert.equal(failed.status,'failed');assert.equal((await db.query('SELECT rep_total FROM staff_report_compare_person WHERE analysis_id=$1 AND person_code=$2',[newer.id,code])).rows[0].rep_total,15);
  await service.retry(newer.id);const retried=await service.queue.claim();assert.ok(retried);assert.notEqual(retried.id,failedJob.id);
  await service.process(newer.id,(retried.payload as {version:string}).version,async()=>({data:{overall_comment:'重试成功'},tokens:5,model:'mock'}));await service.queue.finish(retried,{ok:true});
  // Simulate an interrupted task after one language has already been durably stored.
  const created=(await db.query(`INSERT INTO staff_report_analyses(period_start,period_end,status,summary,payload,ai_model,ai_tokens,created_by,created_at) VALUES($1,$2,'running',$3,$4,'mock',7,$5,now()) RETURNING id,created_at::text AS version`,[start,end,JSON.stringify({fingerprint:reportFingerprint(await comparison.compare(start,end))}),JSON.stringify({by_lang:{zh:{overall_comment:'已保存',accuracy_notes:'',per_person:{}}}}),user])).rows[0];ids.push(created.id);
  const languages:string[]=[];await service.process(created.id,created.version,async(_prompt,language)=>{languages.push(language);return {data:{overall_comment:'再開'},tokens:3,model:'mock'};});assert.deepEqual(languages,['ja']);
  const changing=(await db.query(`INSERT INTO staff_report_analyses(period_start,period_end,status,summary,payload,ai_model,ai_tokens,created_at) VALUES($1,$2,'pending',$3,'{}','',0,now()) RETURNING id,created_at::text AS version`,[start,end,JSON.stringify({fingerprint:reportFingerprint(await comparison.compare(start,end))})])).rows[0];ids.push(changing.id);
  let mutated=false;const invalidated=await service.process(changing.id,changing.version,async()=>{if(!mutated){mutated=true;await db.query('UPDATE staff_daily_reports SET p1_cnt=p1_cnt+1,total_cnt=total_cnt+1 WHERE person_code=$1 AND report_date=$2',[code,start]);}return {data:{overall_comment:'不得配给新数字的旧评语'},tokens:1,model:'mock'};});
  assert.equal(invalidated.status,'failed');assert.deepEqual((await service.get(changing.id)).payload.by_lang,{});
  const admin=request.agent(app.getHttpServer());const login=await admin.post('/api/auth/login').send({username:'analysis-p3-admin',password}).expect(201);
  await admin.get(`/api/reports/analyses?start=${start}&end=${end}`).expect(200);await admin.post(`/api/reports/analyses/${created.id}/delete`).send({}).expect(403);await admin.post(`/api/reports/analyses/${created.id}/delete`).set('X-CSRF-Token',login.body.csrfToken).send({}).expect(201);
  // Pending records are reconciled after a crash between DB commit and file-queue publication.
  const orphan=(await db.query(`INSERT INTO staff_report_analyses(period_start,period_end,status,summary,payload,ai_model,ai_tokens,created_at) VALUES($1,$2,'pending','{}','{}','',0,now()) RETURNING id`,[start,end])).rows[0];ids.push(orphan.id);await service.reconcile();const recovered=await service.queue.claim();assert.ok(recovered);assert.equal((recovered.payload as {id:number}).id,orphan.id);
 }finally{
  await db.query('DELETE FROM staff_report_compare_day WHERE analysis_id=ANY($1::int[])',[ids]);await db.query('DELETE FROM staff_report_compare_person WHERE analysis_id=ANY($1::int[])',[ids]);await db.query('DELETE FROM staff_report_analyses WHERE id=ANY($1::int[])',[ids]);await db.query('DELETE FROM staff_daily_reports WHERE person_code=$1',[code]);await db.query('DELETE FROM person_daily_stats WHERE person_code=$1',[code]);await db.query('DELETE FROM users WHERE id=$1',[user]);await db.query('DELETE FROM persons WHERE code=$1',[code]);await app.close();await rm(directory,{recursive:true,force:true});if(priorDirectory===undefined)delete process.env.NEXT_TASK_DIRECTORY;else process.env.NEXT_TASK_DIRECTORY=priorDirectory;
 }
});
