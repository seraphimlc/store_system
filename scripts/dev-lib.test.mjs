import test from 'node:test';import assert from 'node:assert/strict';import {developmentEnvironment} from './dev-lib.mjs';
const base='NEXT_DATABASE_URL=postgresql://u:p@localhost/store_settle\nNEXT_SESSION_SECRET=abcdefghijklmnopqrstuvwxyz1234567890\nOSS_ACCESS_KEY_SECRET=private\n';
test('dev uses local precedence, a private shared queue, source Worker and frontend credential isolation',()=>{
 const original={PATH:'/bin',NEXT_PORT:'3010'},snapshot={...original};const result=developmentEnvironment('/project',base,'NEXT_PORT=3001\nNEXT_LEGACY_UPLOAD_HOST_DIRECTORY=/private/uploads',original);
 assert.equal(result.urls.admin,'http://127.0.0.1:8001/admin/');assert.equal(result.urls.staff,'http://127.0.0.1:8002/'); assert.equal(result.port,3010);assert.equal(result.backend.NEXT_WORKER_SOURCE,'true');assert.equal(result.backend.NEXT_WORKER_RECONCILE,'false');assert.equal(result.backend.NEXT_TASK_DIRECTORY,'/project/data/refactor/dev-jobs');assert.equal(result.backend.NEXT_LEGACY_UPLOAD_DIRECTORY,'/private/uploads');assert.equal(result.frontend.NEXT_DEV_API_TARGET,'http://127.0.0.1:3010');assert.equal(result.frontend.OSS_ACCESS_KEY_SECRET,undefined);assert.equal(result.frontend.NEXT_DATABASE_URL,undefined);assert.deepEqual(original,snapshot);
 assert.equal(result.backend.NEXT_DATABASE_URL,'postgresql://u:p@localhost/store_settle');assert.equal(result.backend.NEXT_COOKIE_SECURE,'false');
});
test('dev rejects remote/other DB, invalid secrets and port conflicts without changing configuration',()=>{
 for(const value of ['postgresql://u:p@production/store_settle','postgresql://u:p@localhost/other_database','postgresql://u:p@localhost/store_settle_refactor_p1','sqlite:///tmp/test'])assert.throws(()=>developmentEnvironment('/project',base,'',{NEXT_DATABASE_URL:value}));
 for(const port of ['8001','8002','0','NaN','65536'])assert.throws(()=>developmentEnvironment('/project',base,'',{NEXT_PORT:port}));assert.throws(()=>developmentEnvironment('/project',base,'',{NEXT_SESSION_SECRET:'short'}));
});
