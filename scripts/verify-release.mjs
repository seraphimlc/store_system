/** Local release failure/switch/rollback exercise; no schema or employee writes. */
import { spawnSync } from 'node:child_process';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import http from 'node:http';
const [previous, candidate] = process.argv.slice(2);
if (![previous,candidate].every(x => /^[a-z0-9-]+$/.test(x ?? ''))) throw new Error('Expected two built release IDs');
async function current() {
 // The deploy intentionally reloads nginx. Use a fresh socket for each post-switch probe.
 return new Promise((resolve,reject)=>{
  const req=http.get('http://localhost:8000/api/health/ready',{agent:false},res=>{
   res.resume();if(res.statusCode!==200)reject(new Error('Existing local deployment not healthy'));
   else resolve(res.headers['x-store-release']);
  });req.on('error',reject);req.setTimeout(10000,()=>req.destroy(new Error('Health timeout')));
 });
}

function command(kind,release,success=true) {
 const r=spawnSync('node',['scripts/release.mjs',kind,'--env','local','--release',release],{encoding:'utf8',timeout:150000});
 if(success ? r.status!==0 : r.status===0)throw new Error(`Unexpected ${kind} status ${r.status}; inspect local command output`);
 return r.status;
}
if(await current()!==previous)throw new Error('Initial release mismatch');
const html=resolve('.release',candidate,'gateway-context/html/index.html');
const bytes=await readFile(html);
try {
 await writeFile(html,Buffer.concat([bytes,Buffer.from('\n<!-- invalid fixture -->')]));
 command('deploy',candidate,false);
 if(await current()!==previous)throw new Error('Failed candidate changed traffic');
} finally {await writeFile(html,bytes);}
command('deploy',candidate);
if(await current()!==candidate)throw new Error('Candidate did not switch');
const manifest=JSON.parse(await readFile(resolve('.release',candidate,'manifest.json'),'utf8'));
for(const item of manifest.assets){
 const url=new URL(item.url);const r=await fetch('http://localhost:8000'+url.pathname);
 if(!r.ok||!r.headers.get('cache-control')?.includes('immutable'))throw new Error('Static resource or cache check failed');
}
const htmlResponse=await fetch('http://localhost:8000/admin/zone-tasks');
if(htmlResponse.headers.get('cache-control')!=='no-cache')throw new Error('HTML cache header mismatch');
const oldManifest=JSON.parse(await readFile(resolve('.release',previous,'manifest.json'),'utf8'));
for(const item of oldManifest.assets){ const r=await fetch('http://localhost:8000'+new URL(item.url).pathname);if(!r.ok)throw new Error('Old lazy-loaded chunk was lost during switch'); }
command('rollback',previous);
if(await current()!==previous)throw new Error('Rollback did not restore release');
for(const item of manifest.assets){const r=await fetch('http://localhost:8000'+new URL(item.url).pathname);if(!r.ok)throw new Error('Candidate chunks lost during rollback');}
command('deploy',candidate);
if(await current()!==candidate)throw new Error('Redeployment did not restore candidate');
const result={previous,candidate,checksumFailurePreservedTraffic:true,assetCount:manifest.assets.length,htmlCache:'no-cache',assetCache:'immutable',rollback:true,redeploy:true};
await mkdir('data/refactor',{recursive:true});
await writeFile('data/refactor/p2-release.json',JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
