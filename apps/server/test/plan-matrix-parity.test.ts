import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import {spawnSync} from 'node:child_process';
import {createApp} from '../src/main';import {Database} from '../src/database';import {PlansService} from '../src/plans';import {jstToday} from '../src/business-dates';import {periodOf,periodDays} from '../src/plan-rules';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('attendance matrix person inclusion, marks and all daily counters match legacy on the same isolated schema',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);const app=await createApp({databaseUrl:url!,sessionSecret:'matrix-parity-secret-at-least-40-characters',cookieSecure:false,port:0,host:'127.0.0.1'});const db=app.get(Database);const service=app.get(PlansService);const codes=['P3-MATRIX-A','P3-MATRIX-B','P3-MATRIX-C','P3-MATRIX-D'];const today=jstToday(),key=periodOf(today),days=periodDays(key);const users:number[]=[];
 try{
  await db.query("INSERT INTO persons(code,display_name,created_at) SELECT code,code,'2000-01-01' FROM unnest($1::text[]) AS code",[codes]);
  for(const [code,role,status]of [[codes[0],'staff','active'],[codes[1],'leader','leave'],[codes[2],'staff','disabled']]as const)users.push((await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,'unusable-fixture',$1,$1,$2,$3,$4,false,'zh','2000-01-01') RETURNING id`,[code,role,status,status!=='disabled'])).rows[0].id);
  for(const [index,code]of codes.entries()){
   await db.query(`INSERT INTO staff_date_plans(person_code,plan_date,available,reported,source,created_at,updated_at) VALUES($1,$2,$3,false,'web',now(),now()),($1,$4,false,true,'report',now(),now())`,[code,days[0],index%2===0,days.at(-1)]);
  }
  const actual=await service.matrix(key);
  const script=`import json\nfrom app.db import SessionLocal\nfrom app.services.date_plan import admin_matrix\ndef serial(x):\n if isinstance(x,dict):return {str(k):serial(v) for k,v in x.items()}\n if isinstance(x,list):return [serial(v) for v in x]\n return x\ns=SessionLocal()\ntry:print(json.dumps(serial(admin_matrix(s,'${key}')),default=str,ensure_ascii=False))\nfinally:s.close()`;
  const old=spawnLegacyPython(['-c',script],{encoding:'utf8',env:{...process.env,DATABASE_URL:url!.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:')}});assert.equal(old.status,0,old.stderr.slice(-2000));const legacy=JSON.parse(old.stdout);
  assert.deepEqual(actual.rows.map(row=>({code:row.code,name:row.name,submitted:row.submitted,on_cnt:row.on_cnt,off_cnt:row.off_cnt,done_cnt:row.done_cnt,marks:Object.fromEntries(row.days.map(day=>[day.date,day.mark]))})),legacy.rows.map((row:Record<string,unknown>)=>({code:row.person_code,name:row.name,submitted:row.submitted,on_cnt:row.on_cnt,off_cnt:row.off_cnt,done_cnt:row.done_cnt,marks:row.marks})));
  for(const [key,field]of [['free','free_cnt'],['unregistered','none_cnt'],['defaultCount','default_cnt'],['planned','plan_cnt'],['actual','actual_cnt']]as const)assert.deepEqual(Object.fromEntries(actual.summary.map(day=>[day.date,day[key]])),legacy[field]);
  assert.deepEqual(actual.unsubmitted,legacy.unsubmitted);assert.equal(actual.total,legacy.total);
 }finally{await db.query('DELETE FROM staff_date_plans WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[users]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);await app.close();}
});
