import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import request from '../node_modules/supertest';import {hash} from '@node-rs/argon2';import {createApp} from '../src/main';import {Database} from '../src/database';import {jstToday} from '../src/business-dates';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('team reports follow current team leadership independently of account role and reject outsiders and revoked membership',{skip:!url},async()=>{
 assert.match(new URL(url!).pathname,/_refactor_p1$/);const app=await createApp({databaseUrl:url!,sessionSecret:'team-report-test-secret-at-least-40-characters',cookieSecure:false,port:0,host:'127.0.0.1'});const db=app.get(Database);const codes=['P3-TEAM-LEADER','P3-TEAM-MEMBER','P3-TEAM-OUTSIDE'];const ids:number[]=[];let team:number|undefined;const password='team-report-fixture',pw=await hash(password),today=jstToday();
 try{
  await db.query('INSERT INTO persons(code,display_name,created_at) SELECT code,code,now() FROM unnest($1::text[]) AS code',[codes]);
  for(const code of codes)ids.push((await db.query(`INSERT INTO users(username,password_hash,display_name,person_code,role,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,$1,$1,'staff','active',true,false,'zh',now()) RETURNING id`,[code,pw])).rows[0].id);
  team=(await db.query("INSERT INTO bd_team(name,created_at,updated_at) VALUES('P3日报团队',now(),now()) RETURNING id")).rows[0].id;
  await db.query(`INSERT INTO bd_team_member(team_id,person_code,role,start_date,created_at) VALUES($1,$2,'leader','2026-01-01',now()),($1,$3,'member','2026-01-01',now())`,[team,codes[0],codes[1]]);
  await db.query(`INSERT INTO staff_daily_reports(person_code,report_date,area,p1_cnt,p2_cnt,total_cnt,submitted_at,created_at) VALUES($1,$2,'可见日报',3,2,5,now(),now()),($3,$2,'不可见日报',8,1,9,now(),now())`,[codes[1],today,codes[2]]);
  const leader=request.agent(app.getHttpServer()),member=request.agent(app.getHttpServer());await leader.post('/api/auth/login').send({username:codes[0],password}).expect(201);await member.post('/api/auth/login').send({username:codes[1],password}).expect(201);
  const mine=await leader.get('/api/reports/mine').expect(200);assert.equal(mine.body.canViewTeam,true);
  const visible=await leader.get('/api/reports/team?personCode='+codes[1]).expect(200);assert.equal(visible.body.summary.points,7);assert.equal(visible.body.items[0].area,'可见日报');assert.ok(!JSON.stringify(visible.body).includes('不可见日报'));
  await leader.get('/api/reports/team?personCode='+codes[2]).expect(403);await member.get('/api/reports/team').expect(403);
  await db.query('UPDATE bd_team_member SET end_date=$1 WHERE team_id=$2 AND person_code=$3',[today,team,codes[1]]);await leader.get('/api/reports/team?personCode='+codes[1]).expect(403);
  await db.query('UPDATE bd_team_member SET end_date=$1 WHERE team_id=$2 AND person_code=$3',[today,team,codes[0]]);await leader.get('/api/reports/team').expect(403);
 }finally{await db.query('DELETE FROM staff_daily_reports WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM bd_team_member WHERE team_id=$1',[team]);await db.query('DELETE FROM bd_team WHERE id=$1',[team]);await db.query('DELETE FROM users WHERE id=ANY($1::int[])',[ids]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);await app.close();}
});
