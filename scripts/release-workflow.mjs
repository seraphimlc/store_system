/** Local release evidence/preflight. No uploads, SSH, production writes or implicit deployments. */
import {readFile,writeFile,mkdir,stat,access,lstat,chmod,rm} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync,spawn} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
export function releaseDirectory(release,base='.release'){
 if(!/^prod-\d{8}-\d{6}$/.test(release??''))throw Error('Use an immutable production release ID');
 return resolve(base,release);
}
export async function preflight(release,{base='.release',git=true}={}){
 const dir=releaseDirectory(release,base),m=JSON.parse(await readFile(join(dir,'manifest.json'),'utf8'));
 if(m.id!==release||m.releaseTag!==release||m.dirty!==false||m.platform!=='linux/amd64'||m.artifactKind!=='image-overlay-v1'||!/^[a-f0-9]{40}$/.test(m.sourceCommit??''))throw Error('Committed/tagged amd64 overlay manifest required');
 if(git&&execFileSync('git',['rev-parse',release+'^{}'],{encoding:'utf8'}).trim()!==m.sourceCommit)throw Error('Tag does not match sourceCommit');
 const names=new Set();
 for(const f of m.files){
  if(names.has(f.path)||f.path.startsWith('/')||f.path.split('/').some(s=>!s||s==='.'||s==='..'))throw Error('Unsafe or duplicate overlay path');names.add(f.path);
  const p=join(dir,'overlay',f.path),s=await lstat(p);if(!s.isFile())throw Error('Overlay must contain regular files');const bytes=await readFile(p);
  if(hash(bytes)!==f.sha256||bytes.length!==f.size)throw Error('Overlay checksum mismatch: '+f.path);
 }
 for(const migration of m.migrations??[])if(!names.has('migrations/'+migration))throw Error('Migration missing from encrypted overlay');
 for(const f of m.assets){const p=join(dir,'assets',f.app,f.path);if(!['staff','admin'].includes(f.app)||f.path.startsWith('/')||f.path.split('/').some(s=>s==='..'))throw Error('Unsafe asset path');const bytes=await readFile(p);if(hash(bytes)!==f.sha256||bytes.length!==f.size)throw Error('Asset checksum mismatch: '+f.path);}
 // Re-run no build: this checks the exact frozen assembly, including its shared package contracts.
 const compiler=resolve('node_modules/.bin/tsc');
 if(git)for(const app of new Set(m.assets.map(f=>f.app)))execFileSync(compiler,['--noEmit','-p',join(dir,'source/apps',app,'tsconfig.json')],{stdio:'pipe',timeout:120000});
 return {ok:true,release,sourceCommit:m.sourceCommit,overlayFiles:m.files.length,assets:m.assets.length,scope:m.scope??null};
}
/** Stage the complete small helper dependency set; all files are private outside Git. */
export async function stageSupport(release,{base='.release',root=process.cwd(),git=true}={}){
 const dir=releaseDirectory(release,base),support=join(dir,'support');await mkdir(support,{recursive:true,mode:0o700});
 const files=[['scripts/encrypted-release-bundle.mjs','encrypted-release-bundle.mjs'],['scripts/asset-storage.mjs','asset-storage.mjs'],['scripts/deploy-attendance.py','deploy-attendance.py'],['scripts/attendance-release-smoke.cjs','attendance-smoke.cjs'],[join(dir,'encrypted-upload.json'),'encrypted-upload.json'],[join(dir,'decrypt-key.json'),'decrypt-key.json']];
 const inventory=[];
 for(const [path,name]of files){const source=resolve(root,path);await access(source);const bytes=await readFile(source);if(git&&path.startsWith('scripts/')){const committed=execFileSync('git',['show',release+'^{}:'+path],{cwd:root});if(hash(bytes)!==hash(committed))throw Error('Helper differs from tagged source: '+name);}if(name==='decrypt-key.json'&&(await stat(source)).mode&0o077)throw Error('Decrypt key permissions must be 600');await writeFile(join(support,name),bytes,{mode:0o600});await chmod(join(support,name),0o600);inventory.push({name,size:bytes.length,sha256:hash(bytes),secret:name==='decrypt-key.json'});}
 // Confirm static local imports of the decrypt entry exist before spending any upload/SSH round trips.
 const code=await readFile(join(support,'encrypted-release-bundle.mjs'),'utf8');
 for(const [,path]of code.matchAll(/from\s*['"]\.\/([^'"]+)['"]/g))if(!inventory.some(f=>f.name===path))throw Error('Missing helper dependency: '+path);
 const receipt={ok:true,release,directory:support,files:inventory};await writeFile(join(support,'support-files.json'),JSON.stringify(receipt,null,2),{mode:0o600});return receipt;
}
export function summarizeTimeline(entries,now=Date.now()){
 const sorted=[...entries].sort((a,b)=>Date.parse(a.startedAt)-Date.parse(b.startedAt));
 if(!sorted.length)return {wallSeconds:0,activeSeconds:0,gapSeconds:0,failed:0,running:0};
 let cursor=Date.parse(sorted[0].startedAt),first=cursor,active=0;
 for(const e of sorted){const start=Date.parse(e.startedAt),end=e.endedAt?Date.parse(e.endedAt):e.status==='running'?now:start+(e.seconds??0)*1000;if(!Number.isFinite(start)||!Number.isFinite(end)||end<start)throw Error('Invalid timeline entry');active+=Math.max(0,end-Math.max(cursor,start));cursor=Math.max(cursor,end);}
 return {wallSeconds:(cursor-first)/1000,activeSeconds:active/1000,gapSeconds:(cursor-first-active)/1000,failed:sorted.filter(e=>e.status==='failed').length,running:sorted.filter(e=>e.status==='running').length};
}
/** Append checkpoints immediately; report wall time, not only command CPU/execution time. */
export async function checkpoint(release,phase,status,{base='.release',now=new Date()}={}){
 if(!/^[a-z][a-z0-9-]{0,63}$/.test(phase)||!['running','passed','failed'].includes(status))throw Error('Invalid checkpoint');
 const dir=releaseDirectory(release,base);await mkdir(dir,{recursive:true});const path=join(dir,'workflow-timeline.json');let entries=[];try{entries=JSON.parse(await readFile(path,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 if(status==='running'){if(entries.some(e=>e.status==='running'))throw Error('Finish the active phase first');entries.push({phase,status,startedAt:now.toISOString()});}
 else{const e=entries.at(-1);if(!e||e.phase!==phase||e.status!=='running')throw Error('No matching active phase');Object.assign(e,{status,endedAt:now.toISOString(),seconds:(now-Date.parse(e.startedAt))/1000});}
 await writeFile(path,JSON.stringify(entries,null,2),{mode:0o600});return summarizeTimeline(entries,now.getTime());
}
/** One local invocation records an entire existing command; never retries a failed command. */
export async function runPhase(release,phase,command,args=[],options={}){
 if(!command)throw Error('Command required');await checkpoint(release,phase,'running',options);
 try{
  await new Promise((resolve,reject)=>{const child=spawn(command,args,{stdio:'inherit',shell:false});child.once('error',reject);child.once('exit',(code,signal)=>code===0?resolve():reject(Error('Release phase failed: '+(signal??code))));});
  return await checkpoint(release,phase,'passed',options);
 }catch(error){await checkpoint(release,phase,'failed',options);throw error;}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 const [action,release,phase,status,...args]=process.argv.slice(2);let result;
 if(action==='run')result=await runPhase(release,phase,status,args);
 else if(action==='preflight')result=await preflight(release);
 else if(action==='support')result=await stageSupport(release);
 else if(action==='support-clean'){await rm(join(releaseDirectory(release),'support/decrypt-key.json'),{force:true});result={ok:true,stagedKeyRemoved:true};}
 else if(action==='checkpoint')result=await checkpoint(release,phase,status);
 else if(action==='report'){const dir=releaseDirectory(release);result=summarizeTimeline(JSON.parse(await readFile(join(dir,'workflow-timeline.json'),'utf8')));}
 else throw Error('Usage: release-workflow.mjs preflight|support|support-clean|report RELEASE; checkpoint RELEASE PHASE running|passed|failed; run RELEASE PHASE COMMAND [ARGS...]');
 console.log(JSON.stringify(result,null,2));
}
