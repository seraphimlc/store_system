import test from 'node:test';import assert from 'node:assert/strict';import {browserSuites,sourceConnection,temporaryDatabaseName,dropStatement,testEnvironment} from './test-isolation-lib.mjs';
test('disposable tests leave application configuration unchanged and cannot target or drop a baseline',()=>{
 const original={NEXT_DATABASE_URL:'postgresql://u:p@localhost/store_settle',NEXT_TEST_DATABASE_URL:'postgresql://u:p@localhost/store_settle'},snapshot={...original};
 const source=sourceConnection(original.NEXT_TEST_DATABASE_URL),name=temporaryDatabaseName();assert.equal(source.database,'store_settle');assert.match(name,/_refactor_p1$/);assert.match(dropStatement(name,source.database),/^DROP DATABASE IF EXISTS "store_test_/);
 for(const value of ['store_settle','store_settle_refactor_p1','postgres',name+';DROP','store_test_bad_refactor_p1'])assert.throws(()=>dropStatement(value,source.database));
 for(const value of ['postgresql://u:p@production/store_settle_refactor_p1','postgresql://u:p@localhost/store_settle_refactor_p1','mysql://u:p@localhost/store_settle_refactor_p1'])assert.throws(()=>sourceConnection(value));
 const target='postgresql://u:p@localhost/'+name,result=testEnvironment(original,target);assert.deepEqual(original,snapshot);assert.equal(result.NEXT_DATABASE_URL,target);assert.equal(result.NEXT_TEST_DATABASE_URL,target);assert.match(result.DATABASE_URL,/^postgresql\+psycopg2:/);assert.equal(result.NEXT_TEST_ISOLATED,'1');
});

test('browser runtime cannot inherit live database, provider secrets or shared job storage',async()=>{
 const {browserEnvironment}=await import('./test-isolation-lib.mjs'),name=temporaryDatabaseName(),url='postgresql://u:p@127.0.0.1/'+name;
 const original={PATH:'/bin',AI_API_KEY:'live-key',GOOGLE_PUBLIC_API_KEY:'live-map',NEXT_DATABASE_URL:'production',NEXT_TASK_DIRECTORY:'/shared/jobs'},result=browserEnvironment(original,url,'/tmp/browser-test');
 assert.equal(result.NEXT_DATABASE_URL,url);assert.equal(result.NEXT_TEST_DATABASE_URL,url);assert.equal(result.NEXT_TASK_DIRECTORY,'/tmp/browser-test');assert.equal(result.AI_API_KEY,undefined);assert.equal(result.GOOGLE_PUBLIC_API_KEY,undefined);assert.equal(original.NEXT_DATABASE_URL,'production');
 for(const invalid of ['postgresql://u:p@localhost/store_settle_refactor_p1','postgresql://u:p@production/'+name])assert.throws(()=>browserEnvironment(original,invalid,'/tmp/browser-test'));
});

test('browser suite selection covers core and operations without arbitrary script execution',async()=>{
 const {browserSuites,isolatedBrowser,browserRelease}=await import('./test-isolation-lib.mjs');
 const core=browserSuites('core'),ops=browserSuites('ops');assert.equal(core.length,4);assert.equal(ops.length,6);const maps=browserSuites('maps');assert.equal(maps.length,6);assert.ok(maps.includes('scripts/verify-p5-zone-assets-browser.mjs'));assert.deepEqual(browserSuites(),[...core,...ops,...maps,...browserSuites('oauth')]);assert.equal(new Set(browserSuites()).size,17);assert.throws(()=>browserSuites('../../script'));
 const url='postgresql://u:p@127.0.0.1/'+temporaryDatabaseName(),env={NEXT_DATABASE_URL:url,NEXT_TEST_DATABASE_URL:url,NEXT_TEST_ISOLATED:'1',NEXT_BROWSER_ISOLATED_RUNTIME:'1',NEXT_BROWSER_BASE_URL:'http://127.0.0.1:12345',NEXT_TASK_DIRECTORY:'/tmp/store-browser-test'};
 assert.equal(isolatedBrowser(env),true);assert.deepEqual(await browserRelease(env),{current:'isolated-source'});
 assert.throws(()=>isolatedBrowser({...env,NEXT_DATABASE_URL:'postgresql://u:p@localhost/store_settle_refactor_p1'}));assert.throws(()=>isolatedBrowser({...env,NEXT_BROWSER_BASE_URL:'https://production/'}));assert.equal(isolatedBrowser({}),false);
});

test('release-loaded provider credentials are not inherited by database regression tests',()=>{const target='postgresql://u:p@localhost/'+temporaryDatabaseName(),result=testEnvironment({AI_API_KEY:'live',GOOGLE_PUBLIC_API_KEY:'live',OSS_ACCESS_KEY_SECRET:'live',STORE_FILES_CDN_BASE:'https://cdn.invalid',NEXT_OSS_ACCESS_KEY_ID:'live',PATH:'/bin'},target);for(const key of ['AI_API_KEY','GOOGLE_PUBLIC_API_KEY','OSS_ACCESS_KEY_SECRET','STORE_FILES_CDN_BASE','NEXT_OSS_ACCESS_KEY_ID'])assert.equal(result[key],undefined);assert.equal(result.PATH,'/bin');assert.equal(result.NEXT_DATABASE_URL,target);});

test('candidate financial fixture cleanup can target only its explicitly isolated API/Worker containers',async()=>{
 const {browserContainer}=await import('./test-isolation-lib.mjs'),url='postgresql://u:p@127.0.0.1/'+temporaryDatabaseName(),env={NEXT_TEST_ISOLATED:'1',NEXT_BROWSER_ISOLATED_RUNTIME:'1',NEXT_DATABASE_URL:url,NEXT_TEST_DATABASE_URL:url,NEXT_BROWSER_BASE_URL:'http://127.0.0.1:12345',NEXT_TASK_DIRECTORY:'/tmp/fixture',NEXT_BROWSER_API_CONTAINER:'accept-123-api',NEXT_BROWSER_WORKER_CONTAINER:'accept-123-worker'};
 assert.equal(browserContainer('api','candidate',env),'accept-123-api');assert.equal(browserContainer('worker','candidate',env),'accept-123-worker');assert.throws(()=>browserContainer('api','candidate',{...env,NEXT_BROWSER_API_CONTAINER:'store-next-live-api-1'}));assert.throws(()=>browserContainer('api','candidate',{...env,NEXT_BROWSER_API_CONTAINER:undefined}));assert.equal(browserContainer('api','old-local',{}),'store-next-old-local-api-1');
});

 test('management acceptance uses the existing disposable runtime and a dedicated business-flow suite',()=>{assert.deepEqual(browserSuites('management'),['scripts/verify-management-browser.mjs']);});

test('map workspace uses a bounded dedicated suite',()=>{assert.deepEqual(browserSuites('map-workspace'),['scripts/verify-map-workspace-browser.mjs']);});

test('messages use the existing disposable browser runtime',()=>{assert.deepEqual(browserSuites('messages'),['scripts/verify-p3-messages-browser.mjs']);});

test('H5 grid controls use the dedicated mocked acceptance suite',()=>{assert.deepEqual(browserSuites('h5-grid'),['scripts/verify-h5-grid-controls-browser.mjs']);});

test('file selection accepts only discovered tests and rejects ambiguous/browser selections',async()=>{
 const {selectTestFiles}=await import('./test-isolation-lib.mjs');
 const files=['apps/server/test/auth.test.ts','apps/server/test/mcp.test.ts'];
 assert.deepEqual(selectTestFiles(files,[]),files);
 assert.deepEqual(selectTestFiles(files,['--integration','--file=apps/server/test/mcp.test.ts']),[files[1]]);
 for(const args of [['--file='],['--file=../outside.test.ts'],['--file='+files[0],'--file='+files[1]],['--browser','--file='+files[1]]])assert.throws(()=>selectTestFiles(files,args));
});

test('test name selection requires a discovered-file request and valid bounded regex',async()=>{
 const {testNameArguments}=await import('./test-isolation-lib.mjs');
 assert.deepEqual(testNameArguments([]),[]);
 assert.deepEqual(testNameArguments(['--file=apps/server/test/mcp-task-read.test.ts','--name=schema-only SDK']),['--test-name-pattern','schema-only SDK']);
 for(const args of [['--name=case'],['--file=x','--name='],['--file=x','--name=['],['--file=x','--name=a','--name=b'],['--browser','--file=x','--name=a']])assert.throws(()=>testNameArguments(args));
});

test('OAuth browser suite uses the existing disposable runtime',async()=>{
 const {browserSuites}=await import('./test-isolation-lib.mjs');
 assert.deepEqual(browserSuites('oauth'),['scripts/verify-mcp-oauth-browser.mjs']);
 assert.ok(browserSuites('all').includes('scripts/verify-mcp-oauth-browser.mjs'));
});
