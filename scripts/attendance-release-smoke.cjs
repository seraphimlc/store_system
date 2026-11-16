/** Candidate may use synthetic users only in the restored DB; production is read-only. */
const assert=require('node:assert/strict'),{createRequire}=require('node:module'),r=createRequire('/app/apps/server/dist/apps/server/src/main.js'),{Client}=r('pg'),{hash}=r('@node-rs/argon2'),{randomUUID}=require('node:crypto');
(async()=>{const db=new Client({connectionString:process.env.NEXT_DATABASE_URL});await db.connect();try{
 const name=(await db.query('SELECT current_database() AS name')).rows[0].name,candidate=process.env.ATTENDANCE_MODE==='candidate';
 assert(candidate?name.startsWith('attendance_candidate_'):name==='store_settle');
 const api=async(path,body,auth={})=>{const response=await fetch('http://127.0.0.1:3000/api'+path,{method:body?'POST':'GET',headers:{...auth,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});return {status:response.status,data:await response.json(),cookie:response.headers.get('set-cookie')?.split(';')[0]};};
 assert.equal((await api('/health/ready')).status,200);assert.equal((await api('/attendance')).status,401);
 const count=(await db.query("SELECT (SELECT count(*)::int FROM bd_attendance_event) AS events,(SELECT count(*)::int FROM bd_attendance_makeup) AS requests")).rows[0];
 if(!candidate){console.log(JSON.stringify({ok:true,readOnly:true,health:200,attendanceUnauthenticated:401,counts:count}));return;}
 const code='RELEASE-ATT-'+randomUUID().slice(0,8),username=code.toLowerCase(),password=randomUUID()+'-fixture';await db.query('INSERT INTO persons(code,display_name,created_at) VALUES($1,$1,now())',[code]);
 await db.query("INSERT INTO users(username,password_hash,display_name,role,person_code,status,is_active,must_change_password,lang,created_at) VALUES($1,$2,$1,'staff',$3,'active',true,false,'zh',now())",[username,await hash(password),code]);
 const login=await api('/auth/login',{username,password});assert.equal(login.status,201);const auth={Cookie:login.cookie,'X-CSRF-Token':login.data.csrfToken};
 const body={kind:'out',requestKey:randomUUID(),latitude:35.68,longitude:139.76,accuracy:20,locatedAt:new Date().toISOString()};const one=await api('/attendance/punch',body,auth),two=await api('/attendance/punch',body,auth);assert.equal(one.status,201);assert.equal(two.data.id,one.data.id);
 assert.equal((await api('/attendance/punch',{kind:'in',requestKey:randomUUID()},auth)).status,400);const mine=await api('/attendance?scope=mine',undefined,auth);assert.equal(mine.status,200);assert.equal(mine.data.events.length,1);assert.equal(mine.data.events[0].kind,'out');
 assert.equal((await api('/attendance?scope=all',undefined,auth)).status,403);
 const makeup=await api('/attendance/makeup',{kind:'in',requestKey:randomUUID(),occurredAt:'2026-01-02T04:59:00+09:00',reason:'release isolated fixture'},auth);assert.equal(makeup.status,201);assert.equal(makeup.data.day,'2026-01-01');
 console.log(JSON.stringify({ok:true,syntheticCandidateOnly:true,login:201,punch:201,retryDeduplicated:true,mandatoryLocation:400,scopeBoundary:403,makeup:201,businessDateBoundary:true}));
 }finally{await db.end();}})().catch(e=>{console.error(e.message);process.exit(1)});
