import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {packBundle,publishBundle,bundleTarget,releaseName,publishAssets} from './release-bundle.mjs';
const env={NEXT_OSS_PROVIDER:'gcs',STORE_FILES_OSS_BUCKET:'visitworld-assets',OSS_ACCESS_KEY_ID:'fixture',OSS_ACCESS_KEY_SECRET:'fixture'};
const commands=Object.fromEntries(['HeadObjectCommand','PutObjectCommand'].map(name=>[name,class{constructor(input){this.input=input;this.kind=name;}}]));
async function fixture(run){const root=await mkdtemp(join(tmpdir(),'store-bundle-'));try{const id='prod-fixture',dir=join(root,id);await mkdir(dir);await writeFile(join(dir,'images.tar.gz'),'fixture-image-bytes');await writeFile(join(dir,'runtime.env'),'MUST_NOT_LEAK=secret');await writeFile(join(dir,'manifest.json'),JSON.stringify({id,platform:'linux/amd64',images:{api:'api:fixture',gateway:'gateway:fixture'},privateValue:'MUST_NOT_LEAK'}));await run({root,id,dir});}finally{await rm(root,{recursive:true,force:true});}}
test('public ZIP has an explicit allowlist, archive checksums and no runtime configuration',()=>fixture(async({root,id,dir})=>{
 const b=await packBundle(id,root);assert.equal(b.platform,'linux/amd64');
 const result=spawnSync('python3',['-c',"import sys,zipfile,json,hashlib;z=zipfile.ZipFile(sys.argv[1]);assert sorted(z.namelist())==['SHA256SUMS','images.tar.gz','manifest.json'];m=json.loads(z.read('manifest.json'));assert 'privateValue' not in m;assert m['imageArchive']['sha256']==hashlib.sha256(z.read('images.tar.gz')).hexdigest();assert z.testzip() is None",join(dir,b.file)],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);
}));
test('GCS publish streams bytes with integrity headers and is idempotent',()=>fixture(async({root,id,dir})=>{
 const bundle=await packBundle(id,root);let stored,calls=0;
 const client={async send(command){const i=command.input;if(command.kind==='HeadObjectCommand'){if(!stored)throw Object.assign(new Error(),{$metadata:{httpStatusCode:404}});return stored;}calls++;assert.equal(i.ContentType,'application/zip');assert.equal(i.ContentLength,bundle.size);assert.ok(i.ContentMD5);let size=0;for await(const bytes of i.Body)size+=bytes.length;assert.equal(size,bundle.size);stored={Metadata:i.Metadata,ContentLength:size};}};
 const result=await publishBundle(bundle,join(dir,bundle.file),env,client,commands);assert.ok(result.url.startsWith('https://storage.googleapis.com/visitworld-assets/store-next/releases/'));await publishBundle(bundle,join(dir,bundle.file),env,client,commands);assert.equal(calls,1);
 await writeFile(join(dir,bundle.file),'tampered');await assert.rejects(publishBundle(bundle,join(dir,bundle.file),env,client,commands),/changed/);
}));
test('rejects paths, non-GCS targets, wrong-platform bundles and conflicting remote objects',()=>fixture(async({root,id,dir})=>{
 assert.throws(()=>releaseName('../private'));const b=await packBundle(id,root);assert.throws(()=>bundleTarget(b,{...env,NEXT_OSS_ENDPOINT:'https://elsewhere.invalid'}),/official/);
 const client={async send(){return{ContentLength:b.size,Metadata:{sha256:'different'}};}};await assert.rejects(publishBundle(b,join(dir,b.file),env,client,commands),/differs/);
 const m=JSON.parse(await readFile(join(dir,'manifest.json'),'utf8'));m.platform='linux/arm64';await writeFile(join(dir,'manifest.json'),JSON.stringify(m));await assert.rejects(packBundle(id,root),/amd64/);
}));

test('CDN publication checks local bytes, public bytes and production-origin CORS',()=>fixture(async({root,id,dir})=>{
 const {createHash}=await import('node:crypto');const bytes=Buffer.from('export default 1;'),sha256=createHash('sha256').update(bytes).digest('hex');
 const asset={app:'staff',path:'assets/test.js',size:bytes.length,sha256,url:`https://cdn.visitworld.me/store-next/${id}/staff/assets/test.js`};
 await mkdir(join(dir,'assets/staff/assets'),{recursive:true});await writeFile(join(dir,'assets/staff/assets/test.js'),bytes);await writeFile(join(dir,'manifest.json'),JSON.stringify({id,assetsMode:'cdn',assets:[asset]}));
 let published=0;const storage={async publish(key){assert.equal(key,`store-next/${id}/staff/assets/test.js`);published++;}};
 const settings={...env,STORE_FILES_CDN_BASE:'https://cdn.visitworld.me'};
 const good=async()=>new Response(bytes,{headers:{'access-control-allow-origin':'*','content-type':'application/javascript'}});
 assert.deepEqual(await publishAssets(id,settings,storage,good,root),{release:id,origin:'https://store.visitworld.me',assets:1,verified:true});assert.equal(published,1);
 await assert.rejects(publishAssets(id,settings,storage,async()=>new Response(bytes,{headers:{'content-type':'application/javascript'}}),root),/CORS/);
}));
