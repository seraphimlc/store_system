/** Separate bounded network evidence from deterministic, digest-pinned browser regression. */
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
import {releaseDirectory} from './release-workflow.mjs';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
export async function networkProbe(url,{timeoutMs=5000,fetcher=fetch}={}){
 const started=performance.now();
 try{const response=await fetcher(url,{method:'GET',redirect:'error',signal:AbortSignal.timeout(timeoutMs)});const bytes=Buffer.from(await response.arrayBuffer());if(!response.ok)throw Error('HTTP '+response.status);return {ok:true,url,seconds:(performance.now()-started)/1000,bytes,contentType:response.headers.get('content-type')};}
 catch(error){return {ok:false,url,seconds:(performance.now()-started)/1000,error:error.name==='TimeoutError'?'timeout':error.message};}
}
export function requireNetwork(probes){if(probes.some(p=>!p.ok))throw Error('Network verification failed; do not rerun the complete browser suite or switch production. Diagnose this probe only.');}
export async function loadPinnedAssets(release,{base='.release'}={}){
 const dir=releaseDirectory(release,base),manifest=JSON.parse(await readFile(join(dir,'manifest.json'),'utf8')),assets=new Map();
 if(manifest.id!==release)throw Error('Release mismatch');
 for(const asset of manifest.assets){if(!['staff','admin'].includes(asset.app)||asset.path.startsWith('/')||asset.path.split('/').some(p=>!p||p==='.'||p==='..'))throw Error('Unsafe asset');const bytes=await readFile(join(dir,'assets',asset.app,asset.path));if(bytes.length!==asset.size||hash(bytes)!==asset.sha256)throw Error('Pinned asset digest mismatch');if(assets.has(asset.url))throw Error('Duplicate asset URL');assets.set(asset.url,{bytes,contentType:asset.path.endsWith('.js')?'application/javascript':asset.path.endsWith('.css')?'text/css':asset.path.endsWith('.woff2')?'font/woff2':'application/octet-stream'});}
 return {manifest,assets,dir};
}
export async function prepareBrowserAssets(release,origin,{base='.release',fetcher=fetch,timeoutMs=5000}={}){
 const pinned=await loadPinnedAssets(release,{base}),documents=new Map(),probes=[];
 const url=new URL(origin);if(url.username||url.password||!['http:','https:'].includes(url.protocol))throw Error('Invalid browser origin');
 // Fetch each document and one executable asset exactly once, not once per page/context/retry.
 for(const [path,html]of [['/','index.html'],['/admin/','admin/index.html']]){
  if(!pinned.manifest.html[html])continue;
  const probe=await networkProbe(new URL(path,url).href,{fetcher,timeoutMs});probes.push({...probe,bytes:undefined});requireNetwork([probe]);
  if(hash(probe.bytes)!==pinned.manifest.html[html])throw Error('Deployed HTML differs from release manifest');documents.set(html,probe.bytes);
 }
 for(const app of new Set(pinned.manifest.assets.map(a=>a.app))){const asset=pinned.manifest.assets.find(a=>a.app===app&&a.path.endsWith('.js'));if(!asset)throw Error('No executable asset for '+app);const probe=await networkProbe(asset.url,{fetcher,timeoutMs});probes.push({...probe,bytes:undefined});requireNetwork([probe]);if(hash(probe.bytes)!==asset.sha256)throw Error('CDN response digest mismatch');}
 return {...pinned,documents,probes};
}
export async function attachPinnedAssets(context,pinned,origin){
 const base=new URL(origin).origin;
 await context.route(base+'/**',route=>{
  const request=route.request();if(request.resourceType()!=='document')return route.fallback();
  const key=new URL(request.url()).pathname.startsWith('/admin')?'admin/index.html':'index.html',bytes=pinned.documents.get(key);if(!bytes)throw Error('Unverified HTML requested');return route.fulfill({body:bytes,contentType:'text/html'});
 });
 // API mocks/auth policy remain owned by the calling test; never convert real writes to allowed traffic.
 await context.route('**/*',route=>{const asset=pinned.assets.get(route.request().url());return asset?route.fulfill({body:asset.bytes,contentType:asset.contentType}):route.fallback();});
}
