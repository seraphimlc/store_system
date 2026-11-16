import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import request from '../node_modules/supertest';
import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';
import {Database} from '../src/database';
import {attendanceDay} from '../src/attendance';
import {attendanceSummary} from '../../../packages/domain/src/attendance';
import type {AttendanceEvent} from '../../../packages/contracts/src/attendance';

const url=process.env.NEXT_TEST_DATABASE_URL;
test('attendance day changes at JST 05:00, including month/year and leap-day boundaries',()=>{
 for(const [instant,day]of [
  ['2026-11-16T04:59:59.999+09:00','2026-11-15'],['2026-11-16T05:00:00+09:00','2026-11-16'],
  ['2027-01-01T04:59:59+09:00','2026-12-31'],['2024-03-01T04:59:59+09:00','2024-02-29'],
  ['2026-11-15T20:00:00Z','2026-11-16'],
 ])assert.equal(attendanceDay(new Date(instant)),day);
});
test('attendance summary selects earliest in and latest out with their own positions, retains mistakes and flags out-only',()=>{
 const row=(id:number,kind:'in'|'out',at:string):AttendanceEvent=>({id,code:'test',kind,at,source:'onsite',latitude:id,longitude:id,accuracy:10,locatedAt:at,createdAt:at});
 const events=[row(4,'out','2026-11-16T19:00:00+09:00'),row(2,'in','2026-11-16T09:00:00+09:00'),row(1,'in','2026-11-16T08:00:00+09:00'),row(3,'out','2026-11-16T18:00:00+09:00')];
 const summary=attendanceSummary(events);assert.equal(summary.firstIn,events[2]);assert.equal(summary.lastOut,events[0]);assert.equal(summary.outWithoutIn,false);assert.equal(events[0].id,4);assert.equal(events.length,4);
 assert.equal(attendanceSummary([events[0]]).outWithoutIn,true);assert.equal(attendanceSummary([]).outWithoutIn,false);
});
test('attendance API: mandatory location, server time, retry/concurrency, current-team scope, makeup audit and domain isolation',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');assert.match(new URL(url!).pathname,/_refactor_p1$/);
 const app=await createApp({databaseUrl:url!,sessionSecret:'attendance-fixture-secret-at-least-40-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database);
 // The migration is replay-safe and only applied inside the runner's disposable schema-only database.
 await db.query(await readFile('deploy-next/migrations/010-attendance.sql','utf8'));
 const codes=['ATT-LEADER-A','ATT-LEADER-B','ATT-MEMBER','ATT-OUTSIDER'],ids:number[]=[],teamIds:number[]=[];
 const snapshot=async()=>JSON.stringify((await db.query(`SELECT
  (SELECT count(*) FROM formal_records) AS formal,(SELECT count(*) FROM month_perf_records) AS monthly,
  (SELECT count(*) FROM payroll_period_rows) AS payroll,(SELECT count(*) FROM payroll_payments) AS payments,
  (SELECT count(*) FROM staff_date_plans) AS plans,(SELECT count(*) FROM staff_daily_reports) AS reports`)).rows);
 try{
  const pw=await hash('attendance-fixture-password');
  for(const code of codes)await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$1,now())',[code]);
  for(const [name,role,code]of [['admin','admin',null],['leader-a','leader',codes[0]],['leader-b','leader',codes[1]],['member','staff',codes[2]],['outsider','staff',codes[3]]] as const){
   ids.push((await db.query(`INSERT INTO users(username,password_hash,display_name,role,person_code,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,$1,$3,$4,'active',true,false,'zh',now()) RETURNING id`,['attendance-'+name,pw,role,code])).rows[0].id);
  }
  for(const name of ['ATT-TEAM-A','ATT-TEAM-B'])teamIds.push((await db.query("INSERT INTO bd_team(name,code,note,status,created_at,updated_at) VALUES($1,$1,'','active',now(),now()) RETURNING id",[name])).rows[0].id);
  for(const [index,code,role]of [[0,codes[0],'leader'],[1,codes[1],'leader'],[0,codes[2],'member']] as const)await db.query('INSERT INTO bd_team_member(team_id,person_code,role,start_date,created_at) VALUES($1,$2,$3,current_date,now())',[teamIds[index],code,role]);
  const agents=await Promise.all(['admin','leader-a','leader-b','member','outsider'].map(async name=>{const agent=request.agent(app.getHttpServer()),session=(await agent.post('/api/auth/login').send({username:'attendance-'+name,password:'attendance-fixture-password'}).expect(201)).body;return {agent,post:(path:string,body:unknown)=>agent.post(path).set('X-CSRF-Token',session.csrfToken).send(body)};}));
  const [admin,leaderA,leaderB,member,outsider]=agents;
  const before=await snapshot();
  await request(app.getHttpServer()).get('/api/attendance').expect(401);
  await member.agent.get('/api/attendance?scope=all').expect(403);await member.agent.get('/api/attendance?scope=team').expect(403);await leaderA.agent.get('/api/attendance?scope=all').expect(403);
  const punch=()=>({kind:'in',requestKey:randomUUID(),latitude:35.68,longitude:139.76,accuracy:24,locatedAt:new Date().toISOString()});
  await member.agent.post('/api/attendance/punch').send(punch()).expect(403);
  await admin.post('/api/attendance/punch',punch()).expect(403);
  for(const body of [{kind:'in',requestKey:randomUUID()},{...punch(),latitude:91},{...punch(),accuracy:-1},{...punch(),occurredAt:'2000-01-01T00:00:00Z'},{...punch(),personCode:codes[3]}])await member.post('/api/attendance/punch',body).expect(400);
  assert.equal((await db.query('SELECT count(*)::int AS n FROM bd_attendance_event')).rows[0].n,0);
  const body=punch(),start=Date.now();
  const posted=await Promise.all([member.post('/api/attendance/punch',body).expect(201),member.post('/api/attendance/punch',body).expect(201)]);
  assert.equal(posted[0].body.id,posted[1].body.id);assert.match(posted[0].body.day,/^\d{4}-\d{2}-\d{2}$/);
  await member.post('/api/attendance/punch',{...body,kind:'out'}).expect(409);
  await member.post('/api/attendance/punch',{...punch(),kind:'out'}).expect(201);
  await member.post('/api/attendance/punch',punch()).expect(201);
  await leaderA.post('/api/attendance/punch',punch()).expect(201);
  // Device clock drift must not reject a successful location or change server-recorded punch time.
  for(const locatedAt of ['2020-01-01T00:00:00Z','2099-01-01T00:00:00Z']){
   const shifted={...punch(),locatedAt};const saved=(await leaderA.post('/api/attendance/punch',shifted).expect(201)).body;
   const event=(await db.query('SELECT occurred_at,located_at FROM bd_attendance_event WHERE id=$1',[saved.id])).rows[0];
   assert.equal(new Date(event.located_at).getTime(),Date.parse(locatedAt));
   assert.ok(new Date(event.occurred_at).getTime()>=start&&new Date(event.occurred_at).getTime()<=Date.now());
   assert.equal(saved.day,attendanceDay(new Date(event.occurred_at)));
   assert.equal((await leaderA.post('/api/attendance/punch',shifted).expect(201)).body.id,saved.id);
  }
  await outsider.post('/api/attendance/punch',{...punch(),kind:'out'}).expect(201);
  const mine=(await member.agent.get('/api/attendance').expect(200)).body;
  assert.equal(mine.people.length,1);assert.equal(mine.events.length,3);assert.ok(mine.events.every((e:AttendanceEvent)=>e.code===codes[2]&&Date.parse(e.at)>=start));
  const all=(await admin.agent.get('/api/attendance?scope=all').expect(200)).body;assert.equal(attendanceSummary(all.events.filter((e:AttendanceEvent)=>e.code===codes[3])).outWithoutIn,true);
  const teamA=()=>leaderA.agent.get('/api/attendance?scope=team');
  let visible=(await teamA().expect(200)).body;assert.equal(visible.people.length,2);assert.ok(!visible.events.some((e:AttendanceEvent)=>e.code===codes[3]));assert.equal(visible.teams.length,1);
  assert.equal((await leaderB.agent.get('/api/attendance?scope=team').expect(200)).body.people.length,1);
  assert.equal((await leaderA.agent.get('/api/attendance?scope=team&team='+teamIds[1]).expect(200)).body.total,0);
  // Old punches follow the employee's CURRENT team, not a snapshot stored on the punch.
  await db.query('UPDATE bd_team_member SET end_date=current_date WHERE person_code=$1 AND end_date IS NULL',[codes[2]]);
  await db.query("INSERT INTO bd_team_member(team_id,person_code,role,start_date,created_at) VALUES($1,$2,'member',current_date,now())",[teamIds[1],codes[2]]);
  assert.ok(!(await teamA().expect(200)).body.people.some((p:{code:string})=>p.code===codes[2]));
  assert.equal((await leaderB.agent.get('/api/attendance?scope=team').expect(200)).body.events.filter((e:AttendanceEvent)=>e.code===codes[2]).length,3);
  await db.query('UPDATE bd_team_member SET end_date=current_date WHERE person_code=$1 AND end_date IS NULL',[codes[1]]);
  assert.equal((await leaderB.agent.get('/api/attendance?scope=team').expect(200)).body.total,0);
  await member.agent.get('/api/attendance?day=2026-02-30').expect(400);
  // A 04:59 punch belongs to the previous business date; approved makeups carry no fabricated coordinates.
  const makeup={kind:'in',requestKey:randomUUID(),occurredAt:'2026-01-02T04:59:59+09:00',reason:'忘记出勤打卡'};
  const applied=(await member.post('/api/attendance/makeup',makeup).expect(201)).body;assert.equal(applied.day,'2026-01-01');
  assert.equal((await member.post('/api/attendance/makeup',makeup).expect(201)).body.id,applied.id);
  await member.post('/api/attendance/makeup',{...makeup,reason:'改原因'}).expect(409);
  await member.post('/api/attendance/makeup',{...makeup,requestKey:randomUUID(),occurredAt:'2099-01-01T00:00:00Z'}).expect(400);
  await leaderA.post('/api/attendance/makeup/'+applied.id+'/review',{decision:'approved'}).expect(403);
  await admin.agent.post('/api/attendance/makeup/'+applied.id+'/review').send({decision:'approved'}).expect(403);
  const race=await Promise.all(['approved','rejected'].map(decision=>admin.post('/api/attendance/makeup/'+applied.id+'/review',{decision,note:'已核对'})));
  assert.deepEqual(race.map(r=>r.status).sort(),[201,409]);
  const approved=race.find(r=>r.status===201)!.body.status==='approved';
  const history=(await member.agent.get('/api/attendance?day=2026-01-01').expect(200)).body;
  assert.equal(history.events.length,approved?1:0);assert.equal(history.requests[0].note,'已核对');assert.ok(history.requests[0].reviewedAt);
  if(approved){assert.equal(history.events[0].source,'makeup');assert.equal(history.events[0].latitude,null);assert.equal(history.events[0].longitude,null);}
  // Both decisions are covered independently, including 05:00 exact boundary.
  const next=(await member.post('/api/attendance/makeup',{...makeup,requestKey:randomUUID(),occurredAt:'2026-01-02T05:00:00+09:00'}).expect(201)).body;assert.equal(next.day,'2026-01-02');
  await admin.post('/api/attendance/makeup/'+next.id+'/review',{decision:approved?'rejected':'approved'}).expect(201);
  await admin.post('/api/attendance/makeup/'+next.id+'/review',{decision:'approved'}).expect(409);
  const row=(await db.query('SELECT * FROM bd_attendance_event WHERE source=\'makeup\'')).rows[0];assert.equal(row.latitude,null);assert.equal(row.actor_id,ids[0]);
  assert.equal(await snapshot(),before,'attendance must not alter plan, report, payroll or settlement facts');
  await db.query("UPDATE users SET status='disabled',is_active=false WHERE id=$1",[ids[3]]);await member.post('/api/attendance/punch',punch()).expect(401);
 }finally{await app.close();}
});
