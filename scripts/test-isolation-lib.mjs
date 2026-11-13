import {randomBytes} from 'node:crypto';
const TEMPORARY=/^store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/;
export function sourceConnection(value){
 const url=new URL(value);
 if(!['postgres:','postgresql:'].includes(url.protocol)||!['localhost','127.0.0.1','[::1]'].includes(url.hostname)||!url.username)throw Error('Tests require an explicit local PostgreSQL source');
 const database=decodeURIComponent(url.pathname.slice(1));if(database!=='store_settle_refactor_p1')throw Error('Schema source must be the local refactor replica');
 return {url,database,user:decodeURIComponent(url.username)};
}
export function temporaryDatabaseName(){return `store_test_${Date.now()}_${randomBytes(6).toString('hex')}_refactor_p1`;}
export function dropStatement(name,source){if(!TEMPORARY.test(name)||name===source||['store_settle','store_settle_refactor_p1','postgres'].includes(name))throw Error('Refusing to drop a non-temporary database');return `DROP DATABASE IF EXISTS "${name}" WITH (FORCE)`;}
export function testEnvironment(env,url){const clean={...env};for(const key of Object.keys(clean))if(/^(?:AI_|GOOGLE_|OSS_|STORE_FILES_|NEXT_OSS_|NEXT_CDN_)/.test(key))delete clean[key];return {...clean,NEXT_TEST_DATABASE_URL:url,NEXT_DATABASE_URL:url,DATABASE_URL:url.replace(/^postgres(?:ql)?:/,'postgresql+psycopg2:'),NEXT_TEST_ISOLATED:'1'};}

/** Browser services must receive only disposable database URLs and no provider credentials. */
export function browserEnvironment(env,url,directory){
 const target=new URL(url),name=decodeURIComponent(target.pathname.slice(1));
 if(!TEMPORARY.test(name)||!['localhost','127.0.0.1','[::1]'].includes(target.hostname)||!['postgres:','postgresql:'].includes(target.protocol))throw Error('Browser services require a disposable local test database');
 const inherited={};for(const key of ['PATH','HOME','TMPDIR','NEXT_BROWSER_EXECUTABLE'])if(env[key])inherited[key]=env[key];
 return {...testEnvironment(inherited,target.href),NEXT_TASK_DIRECTORY:directory,NEXT_SESSION_SECRET:'disposable-browser-test-session-secret-at-least-40-characters',NEXT_COOKIE_SECURE:'false'};
}

const CORE_BROWSER=['verify-p1-browser.mjs','verify-p3-plans-browser.mjs','verify-p3-messages-browser.mjs','verify-p3-remaining-browser.mjs'];
const MAP_BROWSER=['verify-p5-batch-map-browser.mjs','verify-p5-grid-map-browser.mjs','verify-p5-zone-assets-browser.mjs','verify-p5-region-merge-browser.mjs','verify-p5-region-split-browser.mjs','verify-p5-grid-enable-browser.mjs'];
const OPS_BROWSER=['verify-p5-batch-create-browser.mjs','verify-p5-batch-work-browser.mjs','verify-p5-batch-events-browser.mjs','verify-p5-batch-disable-browser.mjs','verify-p5-batch-photos-browser.mjs','verify-p5-store-proposals-browser.mjs'];
export function browserSuites(group='all'){
 if(group==='oauth')return ['scripts/verify-mcp-oauth-browser.mjs'];
 if(group==='messages')return ['scripts/verify-p3-messages-browser.mjs'];
 if(group==='map-workspace')return ['scripts/verify-map-workspace-browser.mjs'];
 if(group==='h5-grid')return ['scripts/verify-h5-grid-controls-browser.mjs'];
 if(group==='h5')return ['scripts/verify-h5-layout-browser.mjs','scripts/verify-p5-batch-map-browser.mjs'];
 if(group==='management')return ['scripts/verify-management-browser.mjs'];
 if(!['all','core','ops','maps'].includes(group))throw Error('Unknown browser suite');
 return (group==='all'?[...CORE_BROWSER,...OPS_BROWSER,...MAP_BROWSER,'verify-mcp-oauth-browser.mjs']:group==='core'?CORE_BROWSER:group==='ops'?OPS_BROWSER:MAP_BROWSER).map(file=>'scripts/'+file);
}
export function isolatedBrowser(env=process.env){
 if(env.NEXT_BROWSER_ISOLATED_RUNTIME!=='1')return false;
 browserEnvironment(env,env.NEXT_TEST_DATABASE_URL,env.NEXT_TASK_DIRECTORY);
 const base=new URL(env.NEXT_BROWSER_BASE_URL);
 if(env.NEXT_TEST_ISOLATED!=='1'||env.NEXT_DATABASE_URL!==env.NEXT_TEST_DATABASE_URL||base.protocol!=='http:'||!['localhost','127.0.0.1','[::1]'].includes(base.hostname)||!env.NEXT_TASK_DIRECTORY)throw Error('Browser fixture and services must share the disposable runtime');
 return true;
}
export async function browserRelease(env=process.env){
 if(isolatedBrowser(env)){const current=env.NEXT_BROWSER_RELEASE??'isolated-source';if(!/^[a-z0-9-]+$/.test(current))throw Error('Invalid isolated release');return {current};}
 const {readFile}=await import('node:fs/promises');return JSON.parse(await readFile('.release/local-state.json','utf8'));
}

/** Fixture maintenance must address only its own Docker containers in candidate acceptance. */
export function browserContainer(kind,release,env=process.env){
 if(!['api','worker'].includes(kind))throw Error('Unknown browser container role');
 if(isolatedBrowser(env)){const name=env[kind==='api'?'NEXT_BROWSER_API_CONTAINER':'NEXT_BROWSER_WORKER_CONTAINER'];if(!new RegExp('^accept-[0-9]+-'+kind+'$').test(name??''))throw Error('Explicit disposable browser container required');return name;}
 if(!/^[a-z0-9-]+$/.test(release))throw Error('Invalid release');return 'store-next-'+release+'-'+kind+'-1';
}

/** Select only already-discovered test files; never execute an arbitrary --file path. */
export function selectTestFiles(files,args){
 const selections=args.filter(arg=>arg.startsWith('--file='));
 if(selections.length>1||selections.length&&args.includes('--browser'))throw Error('Use one --file only for Node test suites');
 if(!selections.length)return files;
 const selected=selections[0].slice(7);
 if(!files.includes(selected))throw Error('--file must select an existing test in the requested suite');
 return [selected];
}

/** Node's name filter stays paired with one discovered file, never the browser fixture suite. */
export function testNameArguments(args){
 const names=args.filter(arg=>arg.startsWith('--name='));
 if(!names.length)return [];
 if(names.length!==1||!args.some(arg=>arg.startsWith('--file='))||args.includes('--browser'))throw Error('Use one --name with a Node --file');
 const name=names[0].slice(7);
 if(!name||name.length>256)throw Error('Test name pattern is empty or too long');
 new RegExp(name);
 return ['--test-name-pattern',name];
}
