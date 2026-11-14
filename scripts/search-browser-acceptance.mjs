/** Search-stage browser gate on existing local Vite only; child suites mock every API. */
import {spawn} from 'node:child_process';import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8002',url=new URL(base);
if(url.protocol!=='http:'||!['localhost','127.0.0.1'].includes(url.hostname))throw Error('A local Vite frontend is required');
const suites=['input','suggestions','feedback','highlight','completion','scope'],results=[];
for(const suite of suites){const script='scripts/verify-search-'+suite+'-browser.mjs';let stdout='',stderr='';
 const code=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,[script],{env:{...process.env,NEXT_BROWSER_BASE_URL:base},stdio:['ignore','pipe','pipe']});child.stdout.on('data',b=>stdout+=b);child.stderr.on('data',b=>stderr+=b);child.once('error',reject);child.once('exit',resolve);});
 const records=stdout.split('\n').filter(line=>line.startsWith('{')).map(line=>{try{return JSON.parse(line);}catch{return null;}}).filter(Boolean),report=records.find(r=>r.ok===true);
 const ok=code===0&&!!report&&report.checks?.length===2&&!report.pageErrors?.length;
 results.push({suite,script,exitCode:code,ok,...(report?{checks:report.checks,pageErrors:report.pageErrors}:{}),...(ok?{}:{failure:stderr.slice(-3000)})});
 console.log(JSON.stringify({suite,ok,exitCode:code}));if(!ok)break;
}
const report={ok:results.length===suites.length&&results.every(r=>r.ok),frontend:base,productionAccessed:false,databaseWrites:false,dockerDeployed:false,results};
await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});const file='data/refactor/search-evaluation/browser-stage-'+Date.now()+'.json';await writeFile(file,JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify({evidence:file,ok:report.ok}));if(!report.ok)process.exitCode=1;
