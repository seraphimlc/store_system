import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import request from '../node_modules/supertest';
import { hash } from '@node-rs/argon2';
import ExcelJS from 'exceljs';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { jstToday, validDate } from '../src/business-dates';
import { compareDaily, reportPoints, scoreShare } from '../src/report-comparison';
const url=process.env.NEXT_TEST_DATABASE_URL;
function offset(day:string,n:number){return new Date(Date.parse(day+'T00:00:00Z')+n*86400000).toISOString().slice(0,10);}
test('report arithmetic preserves points, two different ratios, absolute errors and JST boundaries',()=>{
 assert.equal(reportPoints(6,2),10);assert.equal(scoreShare(6,2),0.4);assert.equal(scoreShare(0,0),null);
 assert.equal(jstToday(new Date('2026-11-10T15:00:00Z')),'2026-11-11');assert.equal(validDate('2026-02-29'),false);
 const sys=[1,2,3].map(day=>({person_code:'A',name:'A',date:`2026-11-0${day}`,p1:10,p2:0,total:10}));
 const rep=[{...sys[0],p1:15,total:15},{...sys[1],p1:5,total:5},{...sys[2],date:'2026-11-04'}];
 const result=compareDaily(sys,rep,'2026-11-01','2026-11-04');assert.equal(result.persons[0].acc,0.5);assert.equal(result.persons[0].gaps,1);assert.equal(result.persons[0].abs_dt,10);assert.equal(result.persons[0].d_total,0);
});
test('reports: one per JST day, atomic plan sync, comparison refresh, private feedback and formal-date locks',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'daily-report-test-only-at-least-40-characters',cookieSecure:false,port:0,host:'127.0.0.1'});
 const db=app.get(Database);const ids:Record<string,number>={};const code='P3-REPORT-A',other='P3-REPORT-B';const today=jstToday(),yesterday=offset(today,-1),before=offset(today,-2);
 const password='report-fixture-password';const pw=await hash(password);
 try{
  await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$2,now()),($3,$4,now())',[code,'日报验收员工',other,'另一员工']);
  for(const [key,personCode,role]of [['admin',null,'admin'],['staff',code,'staff'],['other',other,'staff']]as const){ids[key]=(await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,$1,$3,$4,'active',true,false,'zh',now()) RETURNING id`,['reports-p3-'+key,pw,personCode,role])).rows[0].id;}
  const admin=request.agent(app.getHttpServer()),staff=request.agent(app.getHttpServer()),otherStaff=request.agent(app.getHttpServer());
  const a=(await admin.post('/api/auth/login').send({username:'reports-p3-admin',password}).expect(201)).body;
  const s=(await staff.post('/api/auth/login').send({username:'reports-p3-staff',password}).expect(201)).body;
  const o=(await otherStaff.post('/api/auth/login').send({username:'reports-p3-other',password}).expect(201)).body;
  const post=(path:string,body:unknown)=>admin.post(path).set('X-CSRF-Token',a.csrfToken).send(body);
  await staff.get('/api/reports?start='+before+'&end='+today).expect(403);
  await staff.post('/api/reports/mine').set('X-CSRF-Token',s.csrfToken).send({p1:-1,p2:0,mode:'create'}).expect(400);
  await staff.post('/api/reports/mine').send({p1:1,p2:2,mode:'create'}).expect(403);
  const submissions=await Promise.all([staff.post('/api/reports/mine').set('X-CSRF-Token',s.csrfToken).send({p1:6,p2:2,area:'新宿',mode:'create'}),staff.post('/api/reports/mine').set('X-CSRF-Token',s.csrfToken).send({p1:6,p2:2,area:'新宿',mode:'create'})]);assert.deepEqual(submissions.map(r=>r.status).sort(),[201,409]);
  const plan=(await db.query('SELECT reported,source FROM staff_date_plans WHERE person_code=$1 AND plan_date=$2',[code,today])).rows[0];assert.deepEqual(plan,{reported:true,source:'report'});
  await staff.get('/api/reports/mine?personCode='+other).expect(400);
  const mine=await staff.get('/api/reports/mine?month='+today.slice(0,7)).expect(200);assert.equal(mine.body.items[0].points,10);assert.equal(mine.body.items[0].scoreShare,0.4);assert.equal(mine.body.items[0].storeShare,0.25);
  await db.query('INSERT INTO staff_date_plans(person_code,plan_date,available,reported,source,created_at,updated_at) VALUES($1,$2,false,false,\'web\',now(),now())',[code,yesterday]);
  await post('/api/reports/save',{personCode:code,date:yesterday,area:'池袋',p1:3,p2:0}).expect(201);
  const reported=(await db.query('SELECT reported,available FROM staff_date_plans WHERE person_code=$1 AND plan_date=$2',[code,yesterday])).rows[0];assert.deepEqual(reported,{reported:true,available:false});
  await db.query('INSERT INTO person_daily_stats(person_code,ref_date,records,p1,p2,points) VALUES($1,$2,10,6,4,14),($1,$3,4,4,0,4),($4,$2,1,1,0,1)',[code,today,yesterday,other]);
  ids.analysis=(await db.query(`INSERT INTO staff_report_analyses(period_start,period_end,status,summary,payload,ai_model,ai_tokens,created_at) VALUES($1,$2,'done','{}','{"private":"admin-only"}','test',0,now()) RETURNING id`,[before,today])).rows[0].id;
  await staff.post('/api/reports/mine').set('X-CSRF-Token',s.csrfToken).send({p1:5,p2:2,area:'新宿',mode:'update'}).expect(201);
  assert.equal((await db.query('SELECT rep_total FROM staff_report_compare_person WHERE analysis_id=$1 AND person_code=$2',[ids.analysis,code])).rows[0].rep_total,10);
  const compared=await admin.get(`/api/reports/compare?start=${before}&end=${today}`).expect(200);
  for(const kind of ['reports','compare']){
   const exported=await admin.get(`/api/reports/export?start=${before}&end=${today}&kind=${kind}`).buffer(true).parse((response,done)=>{const chunks:Buffer[]=[];response.on('data',chunk=>chunks.push(Buffer.from(chunk)));response.on('end',()=>done(null,Buffer.concat(chunks)));}).expect(200);
   const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(exported.body);assert.equal(workbook.worksheets.length,kind==='reports'?1:2);assert.match(JSON.stringify(workbook.worksheets[0].getSheetValues()),new RegExp(code));
   assert.match(exported.headers['content-disposition'],/filename\*=UTF-8/);
  }

  // Existing Python implementation is only an independent test oracle, never a runtime dependency.
  const script=`import json\nfrom datetime import date\nfrom app.db import SessionLocal\nfrom app.services.report_compare import compare\ns=SessionLocal()\ntry: print(json.dumps(compare(s,date.fromisoformat('${before}'),date.fromisoformat('${today}')),default=str,ensure_ascii=False))\nfinally: s.close()`;
  const old=spawnLegacyPython(['-c',script],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:')}});
  assert.equal(old.status,0,'Legacy oracle must run on the explicitly isolated database');
  const oracle=JSON.parse(old.stdout);assert.deepEqual(compared.body,oracle);
  await post('/api/reports/delete',{personCode:code,date:yesterday}).expect(201);
  const cleared=(await db.query('SELECT reported,available FROM staff_date_plans WHERE person_code=$1 AND plan_date=$2',[code,yesterday])).rows[0];assert.deepEqual(cleared,{reported:false,available:false});
  assert.equal((await db.query('SELECT rep_total FROM staff_report_compare_person WHERE analysis_id=$1 AND person_code=$2',[ids.analysis,code])).rows[0].rep_total,7);
  // Coverage fixtures are entirely synthetic; never read raw_records content.
  ids.import=(await db.query(`INSERT INTO imports(file_name,file_sha256,file_size,stored_path,uploaded_by,uploaded_at,parsed_sheets,ignored_sheets,total_rows,parsed_rows,status,warnings,errors,created_at) VALUES('P3-report-fixture','p3-report-fixture',0,'fixture',$1,now(),'[]','[]',0,0,'parsed','[]','[]',now()) RETURNING id`,[ids.admin])).rows[0].id;
  ids.raw=(await db.query(`INSERT INTO raw_records(import_id,sheet_name,excel_row,store_id_raw,store_name_local_raw,store_name_en_raw,modified_raw,submitter_raw,record_id_raw,original_row,created_at) VALUES($1,'fixture',1,'fixture','fixture','','','','fixture','{}',now()) RETURNING id`,[ids.import])).rows[0].id;
  await db.query('INSERT INTO formal_records(import_id,raw_record_id,person_code,japan_date,created_at) VALUES($1,$2,$3,$4,now())',[ids.import,ids.raw,code,today]);
  await staff.post('/api/reports/mine').set('X-CSRF-Token',s.csrfToken).send({p1:1,p2:0,mode:'update'}).expect(409);
  await post('/api/reports/delete',{personCode:code,date:today}).expect(409);
  const list=await admin.get(`/api/reports?start=${before}&end=${today}&page=999`).expect(200);assert.equal(list.body.items.length,0);assert.equal(list.body.summary.total,7);
  // Expand only the synthetic coverage to permit the stored analysis period.
  await db.query('UPDATE formal_records SET japan_date=$1 WHERE raw_record_id=$2',[before,ids.raw]);
  ids.raw2=(await db.query(`INSERT INTO raw_records(import_id,sheet_name,excel_row,store_id_raw,store_name_local_raw,store_name_en_raw,modified_raw,submitter_raw,record_id_raw,original_row,created_at) VALUES($1,'fixture',2,'fixture2','fixture2','','','','fixture2','{}',now()) RETURNING id`,[ids.import])).rows[0].id;
  await db.query('INSERT INTO formal_records(import_id,raw_record_id,person_code,japan_date,created_at) VALUES($1,$2,$3,$4,now())',[ids.import,ids.raw2,code,today]);
  const feedback=await staff.get('/api/reports/feedback').expect(200);assert.equal(feedback.body.person.person_code,code);assert.ok(feedback.body.days.every((d:{rep_total:number})=>d.rep_total===7||d.rep_total===null));assert.ok(!JSON.stringify(feedback.body).includes('admin-only'));
  const otherFeedback=await otherStaff.get('/api/reports/feedback').expect(200);assert.equal(otherFeedback.body.person.person_code,other);assert.equal(otherFeedback.body.person.rep_total,0);
 }finally{
  await db.query('DELETE FROM staff_report_compare_day WHERE analysis_id=$1',[ids.analysis]);await db.query('DELETE FROM staff_report_compare_person WHERE analysis_id=$1',[ids.analysis]);await db.query('DELETE FROM staff_report_analyses WHERE id=$1',[ids.analysis]);
  await db.query('DELETE FROM formal_records WHERE import_id=$1',[ids.import]);await db.query('DELETE FROM raw_records WHERE import_id=$1',[ids.import]);await db.query('DELETE FROM imports WHERE id=$1',[ids.import]);
  await db.query('DELETE FROM person_daily_stats WHERE person_code=ANY($1::text[])',[[code,other]]);await db.query('DELETE FROM staff_daily_reports WHERE person_code=ANY($1::text[])',[[code,other]]);await db.query('DELETE FROM staff_date_plans WHERE person_code=ANY($1::text[])',[[code,other]]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[Object.values(ids).filter(id=>id===ids.admin||id===ids.staff||id===ids.other)]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[[code,other]]);await app.close();
 }
});
