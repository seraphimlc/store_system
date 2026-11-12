import test from 'node:test';import assert from 'node:assert/strict';import {aiLoginCandidates,loginCandidates} from '../src/login-name-model';
test('login candidates accept only requested names, clean romaji and reject injected identity fields',()=>{
 assert.deepEqual([...loginCandidates({'小川逸':' --Ogawa Itsuru-- ','王甲':'wangjia'},new Set(['小川逸','王甲']))],[['小川逸','ogawaitsuru'],['王甲','wangjia']]);
 for(const data of [null,[],{other:'admin'},{'小川逸':{username:'x'}},{'小川逸':true}])assert.throws(()=>loginCandidates(data,new Set(['小川逸'])),/AI_LOGIN_INVALID/);
 assert.equal(loginCandidates({'小川逸':'x'.repeat(81)},new Set(['小川逸'])).size,0);
});
test('optional login model batches, scopes its bounded cache and falls back without a provider call when disabled',async()=>{
 const previous={AI_API_KEY:process.env.AI_API_KEY,AI_MODEL:process.env.AI_MODEL,LOGIN_NAME_AI:process.env.LOGIN_NAME_AI};process.env.AI_API_KEY='login-fixture';process.env.AI_MODEL='login-fixture-model';delete process.env.LOGIN_NAME_AI;
 try{
  const names=Array.from({length:26},(_,index)=>'姓名'+index);let calls=0;
  const model=async(prompt:string)=>{calls++;const requested=JSON.parse(prompt.split('\n').at(-1)!) as string[];assert.ok(requested.length<=25);return {data:Object.fromEntries(requested.map((name,index)=>[name,'romaji-'+index])),tokens:0,model:'mock'};};
  assert.equal((await aiLoginCandidates([...names,names[0]],model)).size,26);assert.equal(calls,2);assert.equal((await aiLoginCandidates(names,async()=>{throw Error('cached');})).size,26);
  process.env.AI_MODEL='another-fixture-model';assert.equal((await aiLoginCandidates(names,async()=>{throw Error('provider failure');})).size,0);
  process.env.LOGIN_NAME_AI='0';assert.equal((await aiLoginCandidates(names,model)).size,0);delete process.env.LOGIN_NAME_AI;delete process.env.AI_API_KEY;assert.equal((await aiLoginCandidates(names,model)).size,0);assert.equal(calls,2);
 }finally{for(const [key,value]of Object.entries(previous))if(value===undefined)delete process.env[key];else process.env[key]=value;}
});

test('model login candidates are unique at account creation and cannot change an existing identity',{skip:!process.env.NEXT_TEST_DATABASE_URL},async()=>{
 const [{createApp},{Database},{EmployeeService}]=await Promise.all([import('../src/main'),import('../src/database'),import('../src/employee-service')]);
 const url=process.env.NEXT_TEST_DATABASE_URL!;assert.match(new URL(url).pathname,/_refactor_p1$/);const saved={AI_API_KEY:process.env.AI_API_KEY,AI_MODEL:process.env.AI_MODEL,LOGIN_NAME_AI:process.env.LOGIN_NAME_AI};process.env.AI_API_KEY='login-account-fixture';process.env.AI_MODEL='account-fixture';delete process.env.LOGIN_NAME_AI;
 const app=await createApp({databaseUrl:url,sessionSecret:'login-model-test-at-least-40-character-secret',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(EmployeeService),codes=['P4-NAME-8101','P4-NAME-8102','P4-NAME-8103','P4-NAME-8104'];
 try{
  let calls=0;const model=async(prompt:string)=>{calls++;assert.equal((await db.query("SELECT count(*)::int AS n FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction'")).rows[0].n,0);return {data:{'姓名候选甲':'p4-ogawa-itsuru','姓名候选乙':'p4-ogawa-itsuru'},tokens:0,model:'mock'};};
  // Each response is constrained to its input; unrelated names reject the whole response.
  const scoped=async(prompt:string)=>{const response=await model(prompt);const names=JSON.parse(prompt.split('\n').at(-1)!) as string[];return {...response,data:Object.fromEntries(Object.entries(response.data).filter(([name])=>names.includes(name)))};};
  const first=await service.create({code:codes[0],name:'姓名候选甲',username:'',password:'fixture123',role:'leader'},scoped);assert.equal(first.username,'p4-ogawa-itsuru');
  const second=await service.create({code:codes[1],name:'姓名候选乙',username:'',password:'fixture123',role:'staff'},scoped);assert.equal(second.username,'p4-ogawa-itsuru8102');assert.equal(calls,2);
  await assert.rejects(()=>service.create({code:codes[0],name:'不能改名',username:'',password:'fixture123',role:'staff'},async()=>{throw Error('duplicate-must-not-call');}),error=>(error as {code:string}).code==='CODE_EXISTS');assert.equal((await db.query('SELECT username,role FROM users WHERE person_code=$1',[codes[0]])).rows[0].username,'p4-ogawa-itsuru');
  const explicit=await service.create({code:codes[2],name:'明确登录名',username:'p4-explicit-login',password:'fixture123',role:'staff'},async()=>{throw Error('explicit-must-not-call');});assert.equal(explicit.username,'p4-explicit-login');
  const fallback=await service.create({code:codes[3],name:'王小明',username:'',password:'fixture123',role:'staff'},async()=>{throw Error('provider-failed');});assert.equal(fallback.username,'wangxiaoming');
  const suggested=await service.propose('姓名候选甲',codes[1],async()=>{throw Error('cache');});assert.equal(suggested.source,'model');assert.equal(suggested.username,'p4-ogawa-itsuru2');
 }finally{await db.query('DELETE FROM users WHERE person_code=ANY($1::text[])',[codes]);await db.query('DELETE FROM persons WHERE code=ANY($1::text[])',[codes]);await app.close();for(const [key,value]of Object.entries(saved))if(value===undefined)delete process.env[key];else process.env[key]=value;}
});
