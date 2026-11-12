/** Public, credential-free Docker image bundles. Never deploys or changes runtime configuration. */
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {lstat,readFile,writeFile,rename,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {storageSettings,assetStorage} from './asset-storage.mjs';
import {assetBase} from './release-lib.mjs';

export function releaseName(value){
 if(!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$/.test(value??''))throw Error('A safe --release ID is required');
 return value;
}
export async function fileDigest(file,algorithm='sha256'){
 const hash=createHash(algorithm);
 for await(const bytes of createReadStream(file))hash.update(bytes);
 return hash.digest(algorithm==='md5'?'base64':'hex');
}
async function regular(file){const info=await lstat(file);if(!info.isFile()||info.isSymbolicLink())throw Error('Bundle input must be a regular file');return info;}
export async function packBundle(id,base=resolve('.release')){
 releaseName(id);const dir=join(base,id),source=join(dir,'images.tar.gz');
 await regular(source);await regular(join(dir,'manifest.json'));
 const original=JSON.parse(await readFile(join(dir,'manifest.json'),'utf8'));
 if(original.id!==id||original.platform!=='linux/amd64')throw Error('Bundle must match an existing linux/amd64 release manifest');
 if(!original.images?.api||!original.images?.gateway)throw Error('Manifest must declare API and gateway images');
 // Whitelist fields: runtime.env, private JSON, database dumps and contexts never enter the public ZIP.
 const manifest={};
 for(const key of ['id','commit','dirty','createdAt','platform','assetsMode','images','imageDigests','html','assets','lockSha256','supportedTaskKinds','sourceRelease']){
  if(original[key]!==undefined)manifest[key]=original[key];
 }
 const imageSha256=await fileDigest(source);
 manifest.imageArchive={file:'images.tar.gz',sha256:imageSha256,size:(await lstat(source)).size};
 const publicDir=join(dir,'bundle-public');await mkdir(publicDir,{recursive:true});
 const manifestFile=join(publicDir,'manifest.json');await writeFile(manifestFile,JSON.stringify(manifest,null,2)+'\n',{mode:0o600});
 const sums=join(publicDir,'SHA256SUMS');await writeFile(sums,`${imageSha256}  images.tar.gz\n${await fileDigest(manifestFile)}  manifest.json\n`,{mode:0o600});
 const zip=join(dir,`images-${id}.zip`),pending=zip+'.tmp';
 const code=`import sys,zipfile\nwith zipfile.ZipFile(sys.argv[1],'w',zipfile.ZIP_STORED,allowZip64=True) as z:\n for p,n in zip(sys.argv[2:],['images.tar.gz','manifest.json','SHA256SUMS']):z.write(p,n)\nwith zipfile.ZipFile(sys.argv[1]) as z:\n if z.testzip() is not None:raise RuntimeError('ZIP CRC failed')\n`;
 const result=spawnSync('python3',['-c',code,pending,source,manifestFile,sums],{encoding:'utf8'});
 if(result.status!==0)throw Error('ZIP packaging/CRC verification failed');
 await rename(pending,zip);
 const bundle={release:id,platform:manifest.platform,file:`images-${id}.zip`,size:(await lstat(zip)).size,sha256:await fileDigest(zip),imageArchiveSha256:imageSha256};
 await writeFile(join(dir,'bundle.json'),JSON.stringify(bundle,null,2)+'\n',{mode:0o600});
 return bundle;
}
export function bundleTarget(bundle,env){
 releaseName(bundle.release);
 const config=storageSettings(env);
 if(config.provider!=='gcs'||config.endpoint!=='https://storage.googleapis.com')throw Error('Image bundles require the official Google Cloud Storage endpoint');
 if(!config.bucket||!config.region||!config.accessKeyId||!config.secretAccessKey)throw Error('GCS bucket, region and HMAC credentials are required');
 if(!/^[a-f0-9]{64}$/.test(bundle.sha256))throw Error('Invalid bundle digest');
 const key=`store-next/releases/${bundle.release}/${bundle.sha256}/${bundle.file}`;
 const url=`https://storage.googleapis.com/${encodeURIComponent(config.bucket)}/${key.split('/').map(encodeURIComponent).join('/')}`;
 return {config,key,url};
}
export async function publishBundle(bundle,file,env,client,commands){
 const {config,key,url}=bundleTarget(bundle,env);
 if((await regular(file)).size!==bundle.size||await fileDigest(file)!==bundle.sha256)throw Error('Local bundle changed; package it again');
 let existing;
 try{existing=await client.send(new commands.HeadObjectCommand({Bucket:config.bucket,Key:key}));}
 catch(error){if(error.$metadata?.httpStatusCode!==404&&error.name!=='NotFound')throw Error('GCS bundle metadata read failed');}
 if(existing){
  if(existing.Metadata?.sha256!==bundle.sha256||Number(existing.ContentLength)!==bundle.size)throw Error('Existing GCS bundle differs');
 }else{
  // A fresh stream per attempt; the SDK must not attempt to replay a consumed stream.
  let uploaded=false;
  for(let attempt=0;attempt<2&&!uploaded;attempt++){
   const stream=createReadStream(file);
   try{await client.send(new commands.PutObjectCommand({Bucket:config.bucket,Key:key,Body:stream,ContentLength:bundle.size,ContentMD5:await fileDigest(file,'md5'),ContentType:'application/zip',CacheControl:'public,max-age=31536000,immutable',Metadata:{sha256:bundle.sha256}}));uploaded=true;}
   catch{if(attempt===1)throw Error('GCS bundle upload failed');}
   finally{stream.destroy();}
  }
 }
 const check=await client.send(new commands.HeadObjectCommand({Bucket:config.bucket,Key:key}));
 if(check.Metadata?.sha256!==bundle.sha256||Number(check.ContentLength)!==bundle.size)throw Error('GCS bundle verification failed');
 return {...bundle,bucket:config.bucket,key,url};
}
export async function publishAssets(id,env,storage,request=fetch,base=resolve('.release')){
 releaseName(id);const dir=join(base,id),manifest=JSON.parse(await readFile(join(dir,'manifest.json'),'utf8'));
 if(manifest.id!==id||manifest.assetsMode!=='cdn')throw Error('A matching CDN release manifest is required');
 const mime={'.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2','.woff':'font/woff','.png':'image/png','.webp':'image/webp','.jpg':'image/jpeg','.json':'application/json'};
 const origin=env.NEXT_PUBLIC_ORIGIN||'https://store.visitworld.me';let count=0;
 for(const asset of manifest.assets){
  if(!['admin','staff'].includes(asset.app)||!asset.path||asset.path.split('/').some(v=>!v||v==='.'||v==='..')||asset.path.includes('\\')||asset.path.endsWith('.map'))throw Error('Invalid public asset path');
  const expected=new URL(asset.path,assetBase(id,asset.app,'cdn',env));
  if(asset.url!==expected.href)throw Error('Asset URL differs from configured CDN');
  const file=join(dir,'assets',asset.app,asset.path);await regular(file);
  if((await lstat(file)).size!==asset.size||await fileDigest(file)!==asset.sha256)throw Error('Public asset checksum mismatch');
  const contentType=Object.entries(mime).find(([extension])=>asset.path.endsWith(extension))?.[1]||'application/octet-stream';
  await storage.publish(decodeURIComponent(expected.pathname.slice(1)),file,asset,contentType);
  const response=await request(asset.url,{headers:{Origin:origin},signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw Error('Public CDN asset unavailable');
  const cors=response.headers.get('access-control-allow-origin');
  if(cors!=='*'&&cors!==origin)throw Error('Public CDN CORS does not allow the production origin');
  const bytes=Buffer.from(await response.arrayBuffer());
  if(bytes.length!==asset.size||createHash('sha256').update(bytes).digest('hex')!==asset.sha256)throw Error('Public CDN content checksum mismatch');
  if(!response.headers.get('content-type')?.startsWith(contentType))throw Error('Public CDN content type mismatch');
  count++;
 }
 return {release:id,origin,assets:count,verified:true};
}
async function main(){
 const [action,...args]=process.argv.slice(2);
 if(!['pack','upload','assets'].includes(action)||args.length!==2||args[0]!=='--release')throw Error('Usage: node scripts/release-bundle.mjs pack|upload|assets --release <id>');
 const id=releaseName(args[1]),dir=resolve('.release',id);
 if(action==='assets'){if(storageSettings(process.env).provider!=='gcs')throw Error('Production assets require GCS');const storage=await assetStorage(process.env);try{console.log(JSON.stringify(await publishAssets(id,process.env,storage),null,2));}finally{storage.close();}return;}
 if(action==='pack'){console.log(JSON.stringify(await packBundle(id),null,2));return;}
 const bundle=JSON.parse(await readFile(join(dir,'bundle.json'),'utf8'));
 if(bundle.release!==id||bundle.file!==`images-${id}.zip`)throw Error('Bundle release mismatch');
 const {config}=bundleTarget(bundle,process.env),commands=await import('@aws-sdk/client-s3');
 const client=new commands.S3Client({region:config.region,endpoint:config.endpoint,forcePathStyle:true,credentials:{accessKeyId:config.accessKeyId,secretAccessKey:config.secretAccessKey},requestChecksumCalculation:'WHEN_REQUIRED',responseChecksumValidation:'WHEN_REQUIRED',maxAttempts:1});
 try{const record=await publishBundle(bundle,join(dir,bundle.file),process.env,client,commands);await writeFile(join(dir,'bundle-upload.json'),JSON.stringify(record,null,2)+'\n',{mode:0o600});console.log(JSON.stringify(record,null,2));}finally{client.destroy();}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)main().catch(error=>{console.error('[bundle] '+error.message);process.exitCode=1;});
