import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import request from '../node_modules/supertest';import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';import {Database} from '../src/database';
import {jstToday} from '../src/business-dates';import {PlansService} from '../src/plans';import {addDays,periodOf,periodBounds,periodDays,planCell,planView,windowState,landingPath} from '../src/plan-rules';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('half-month windows, leap years, new-join exceptions and cell states match legacy rules',()=>{
 const keys=['2026-01-H1','2026-02-H2','2028-02-H2','2026-12-H2'];
 const vectors=[];
 for(const available of [null,false,true])for(const reported of [false,true])for(const past of [false,true])for(const beforeStart of [false,true])for(const inactive of [false,true])for(const assumedOff of [false,true])for(const plannedOn of [false,true])vectors.push({available,reported,past,beforeStart,inactive,assumedOff,plannedOn});
 const script=`import json,sys\nfrom app.services.date_plan import period_bounds,period_window,cell_state\nx=json.load(sys.stdin)\nprint(json.dumps({'periods':[[str(d) for d in (*period_bounds(k),*period_window(k))] for k in x['keys']],'cells':[cell_state(v['available'],v['reported'],past=v['past'],before_start=v['beforeStart'],inactive=v['inactive'],assumed_off=v['assumedOff'],planned_on=v['plannedOn']) for v in x['vectors']]}))`;
 const oracle=spawnLegacyPython(['-c',script],{input:JSON.stringify({keys,vectors}),encoding:'utf8',env:{...process.env,DATABASE_URL:'sqlite://'}});assert.equal(oracle.status,0);
 const expected=JSON.parse(oracle.stdout);assert.deepEqual(keys.map(key=>{const p=periodBounds(key);return [p.start,p.end,p.deadline,p.openAt,p.closeAt];}),expected.periods);
 assert.deepEqual(vectors.map(v=>planCell(v.available,v.reported,v)),expected.cells);
 assert.equal(landingPath('staff',true),'/my/work');assert.equal(landingPath('staff',false),'/my/work');assert.equal(landingPath('leader',true),'/my/work');assert.equal(landingPath('staff',true,true),'/my/work');
 assert.equal(periodDays('2026-02-H2').length,13);assert.equal(periodDays('2028-02-H2').length,14);
 assert.equal(windowState('2026-11-H1','2026-11-10','2026-11-10'),'open');assert.equal(windowState('2026-11-H1','2026-11-10','2026-11-01'),'closed');
 assert.equal(planView('2026-11-H1',[],null,'2026-11-10').days[10].state,'off');
});
test('plan save protects history/reported facts; leave preserves manual off days and is reversible',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);const app=await createApp({databaseUrl:url!,sessionSecret:'plan-fixture-at-least-40-characters-secret',cookieSecure:false,port:0,host:'127.0.0.1'});const db=app.get(Database);
 const code='P3-PLAN-A',password='plan-fixture-password',today=jstToday(),key=periodOf(today),bounds=periodBounds(key);let id:number|undefined;
 try{
  // A new roster entry today is allowed to register the current half even after its normal deadline.
  await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$2,now())',[code,'计划验收员工']);
  id=(await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES('plans-p3-staff',$1,'计划验收员工',$2,'staff','active',true,false,'zh',now()) RETURNING id`,[await hash(password),code])).rows[0].id;
  const staff=request.agent(app.getHttpServer());const login=(await staff.post('/api/auth/login').send({username:'plans-p3-staff',password}).expect(201)).body;assert.equal(login.landingPath,'/my/work');
  const post=(path:string,body:unknown)=>staff.post(path).set('X-CSRF-Token',login.csrfToken).send(body);
  await staff.get('/api/plans?key='+key).expect(403);await staff.get('/api/plans/mine?key=invalid').expect(400);
  const view=await staff.get('/api/plans/mine?key='+key).expect(200);assert.equal(view.body.window_open,true);assert.equal(view.body.pending,key);
  const tomorrow=addDays(today,1),manualOff=tomorrow<=bounds.end?tomorrow:today;
  await post('/api/plans/mine',{key,unavailable:[manualOff]}).expect(201);
  assert.equal((await db.query('SELECT available FROM staff_date_plans WHERE person_code=$1 AND plan_date=$2',[code,manualOff])).rows[0].available,false);
  // Put a reported day inside the open period; save cannot overwrite its original plan value.
  await db.query('UPDATE staff_date_plans SET reported=true,available=false WHERE person_code=$1 AND plan_date=$2',[code,today]);
  const saved=await post('/api/plans/mine',{key,unavailable:[]});assert.ok([201,409].includes(saved.status));
  assert.deepEqual((await db.query('SELECT reported,available FROM staff_date_plans WHERE person_code=$1 AND plan_date=$2',[code,today])).rows[0],{reported:true,available:false});
  await post('/api/plans/mine',{key,unavailable:['2020-01-01']}).expect(400);
  const matrix=await app.get(PlansService).matrix(key);assert.ok(matrix.rows.some(row=>row.code===code));assert.equal(matrix.summary.find(day=>day.date===today)?.actual,1);assert.ok(matrix.total>=1);

  // Manual off and reported rows are both protected from automatic leave marks.
  const leaveStart=addDays(today,1),leaveEnd=addDays(today,3);
  await db.query(`INSERT INTO staff_date_plans(person_code,plan_date,available,reported,source,created_at,updated_at) VALUES($1,$2,false,false,'web',now(),now()) ON CONFLICT(person_code,plan_date) DO UPDATE SET available=false,reported=false,source='web',leave_id=NULL`,[code,leaveStart]);
  const leave=await post('/api/plans/leave/start',{start:today,end:leaveEnd,reason:'验收假期'}).expect(201);
  assert.equal((await db.query('SELECT leave_id FROM staff_date_plans WHERE person_code=$1 AND plan_date=$2',[code,leaveStart])).rows[0].leave_id,null);
  const marked=(await db.query('SELECT leave_id,available FROM staff_date_plans WHERE person_code=$1 AND plan_date=$2',[code,leaveEnd])).rows[0];assert.equal(marked.leave_id,leave.body.id);assert.equal(marked.available,false);
  await post('/api/plans/leave/start',{start:today,end:addDays(today,-1),reason:''}).expect(400);
  assert.equal((await db.query("SELECT count(*)::int AS n FROM bd_staff_leave WHERE person_code=$1 AND status='active'",[code])).rows[0].n,1,'invalid replacement must roll back previous leave changes');
  await post('/api/plans/leave/end',{}).expect(201);
  assert.equal((await db.query('SELECT count(*)::int AS n FROM staff_date_plans WHERE leave_id=$1',[leave.body.id])).rows[0].n,0);
  assert.equal((await db.query('SELECT available FROM staff_date_plans WHERE person_code=$1 AND plan_date=$2',[code,leaveStart])).rows[0].available,false);
  assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_log WHERE ref_label=$1',[code])).rows[0].n,2);
  await post('/api/plans/mine',{key:'2020-01-H1',unavailable:[]}).expect(409);
 }finally{await db.query('DELETE FROM bd_log WHERE ref_label=$1',[code]);await db.query('DELETE FROM staff_date_plans WHERE person_code=$1',[code]);await db.query('DELETE FROM bd_staff_leave WHERE person_code=$1',[code]);await db.query('DELETE FROM users WHERE id=$1',[id]);await db.query('DELETE FROM persons WHERE code=$1',[code]);await app.close();}
});
