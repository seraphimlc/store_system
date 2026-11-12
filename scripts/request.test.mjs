import test from 'node:test';import assert from 'node:assert/strict';
import {api,SESSION_EXPIRED_EVENT,ApiFailure,upload} from '../packages/client/src/request.ts';
test('expired authenticated requests clear app sessions; rejected login does not fire expiry',async()=>{
 const original={fetch:globalThis.fetch,window:globalThis.window};const target=new EventTarget();let expired=0;target.addEventListener(SESSION_EXPIRED_EVENT,()=>expired++);globalThis.window=target;
 try{
  globalThis.fetch=async()=>new Response(JSON.stringify({code:'UNAUTHENTICATED',message:'请登录'}),{status:401,headers:{'Content-Type':'application/json'}});
  await assert.rejects(()=>api('/reports'),error=>error instanceof ApiFailure&&error.code==='UNAUTHENTICATED');assert.equal(expired,1);
  await assert.rejects(()=>api('/auth/login',null,{username:'bad',password:'bad'}));assert.equal(expired,1);
  globalThis.fetch=async()=>new Response('<html>bad gateway</html>',{status:502});await assert.rejects(()=>api('/reports'),error=>error.code==='INVALID_RESPONSE');
 }finally{globalThis.fetch=original.fetch;if(original.window===undefined)delete globalThis.window;else globalThis.window=original.window;}
});

test('multipart request preserves browser boundary and sends only CSRF header',async()=>{
 const original=globalThis.fetch;
 try{
  let headers;
  globalThis.fetch=async(_url,init)=>{headers=init.headers;assert.ok(init.body instanceof FormData);return new Response(JSON.stringify({id:1}),{headers:{'Content-Type':'application/json'}});};
  const form=new FormData();form.append('file',new Blob(['fixture']),'fixture.xlsx');
  assert.deepEqual(await upload('/imports/upload',{csrfToken:'fixture',user:{}},form),{id:1});assert.equal(headers['X-CSRF-Token'],'fixture');assert.equal(headers['Content-Type'],undefined);
 }finally{globalThis.fetch=original;}
});
