/** OSS transports ciphertext only. The key file must travel separately over SSH, never in the ZIP. */
import {createCipheriv,createDecipheriv,createHash,randomBytes} from 'node:crypto';
import {createReadStream,createWriteStream} from 'node:fs';
import {readFile,writeFile,mkdir,stat,rename,rm} from 'node:fs/promises';
import {pipeline} from 'node:stream/promises';
import {join,resolve} from 'node:path';import {spawnSync} from 'node:child_process';import {pathToFileURL} from 'node:url';
import {storageSettings} from './asset-storage.mjs';
export async function sha(file){const hash=createHash('sha256');for await(const bytes of createReadStream(file))hash.update(bytes);return hash.digest('hex');}
export async function encryptArchive(source,output,keyFile,release){
 if(!/^prod-\d{8}-\d{6}$/.test(release))throw Error('A formal time release ID is required');
 const digest=await sha(source),key=randomBytes(32),iv=randomBytes(12),aad=Buffer.from(release+':'+digest),cipher=createCipheriv('aes-256-gcm',key,iv);cipher.setAAD(aad);
 await pipeline(createReadStream(source),cipher,createWriteStream(output,{mode:0o600,flags:'wx'}));
 const metadata={algorithm:'aes-256-gcm',release,archiveSha256:digest,archiveBytes:(await stat(source)).size,cipherSha256:await sha(output),cipherBytes:(await stat(output)).size,iv:iv.toString('hex'),tag:cipher.getAuthTag().toString('hex'),aad:aad.toString('base64')};
 await writeFile(keyFile,JSON.stringify({release,key:key.toString('hex')}),{mode:0o600,flag:'wx'});return metadata;
}
export async function decryptArchive(source,output,keyFile,metadata){
 const secret=JSON.parse(await readFile(keyFile,'utf8'));if(secret.release!==metadata.release||metadata.algorithm!=='aes-256-gcm'||await sha(source)!==metadata.cipherSha256||(await stat(source)).size!==metadata.cipherBytes)throw Error('Encrypted archive identity/digest mismatch');
 const decipher=createDecipheriv('aes-256-gcm',Buffer.from(secret.key,'hex'),Buffer.from(metadata.iv,'hex'));decipher.setAAD(Buffer.from(metadata.aad,'base64'));decipher.setAuthTag(Buffer.from(metadata.tag,'hex'));const tmp=output+'.pending';
 try{await pipeline(createReadStream(source),decipher,createWriteStream(tmp,{mode:0o600,flags:'wx'}));if(await sha(tmp)!==metadata.archiveSha256||(await stat(tmp)).size!==metadata.archiveBytes)throw Error('Plain archive digest mismatch');await rename(tmp,output);}catch(error){await rm(tmp,{force:true});throw error;}
}
export async function packEncrypted(release,base=resolve('.release')){
 const dir=join(base,release),manifest=JSON.parse(await readFile(join(dir,'manifest.json'),'utf8'));if(manifest.id!==release||manifest.dirty!==false||manifest.platform!=='linux/amd64'||!manifest.releaseTag||!manifest.sourceCommit)throw Error('Committed/tagged amd64 manifest required');
 const archive=manifest.artifactKind==='image-overlay-v1'?'image-overlay.tar.gz':'images.tar.gz',cipherFile=archive+'.enc';
 const encryption=await encryptArchive(join(dir,archive),join(dir,cipherFile),join(dir,'decrypt-key.json'),release);
 const publicDir=join(dir,'encrypted-public');await mkdir(publicDir,{mode:0o700});await writeFile(join(publicDir,'manifest.json'),JSON.stringify({...manifest,encryption},null,2),{mode:0o600});
 await writeFile(join(publicDir,'SHA256SUMS'),encryption.cipherSha256+'  '+cipherFile+'\n'+await sha(join(publicDir,'manifest.json'))+'  manifest.json\n',{mode:0o600});
 const zip=join(dir,'images-'+release+'.encrypted.zip');const py='import sys,zipfile\nwith zipfile.ZipFile(sys.argv[1],"x",zipfile.ZIP_STORED,allowZip64=True) as z:\n for p,n in zip(sys.argv[3:],[sys.argv[2],"manifest.json","SHA256SUMS"]):z.write(p,n)\nwith zipfile.ZipFile(sys.argv[1]) as z:\n assert z.testzip() is None\n';const r=spawnSync('python3',['-c',py,zip,cipherFile,join(dir,cipherFile),join(publicDir,'manifest.json'),join(publicDir,'SHA256SUMS')],{encoding:'utf8'});if(r.status)throw Error('Encrypted ZIP pack failed');
 const bundle={release,platform:'linux/amd64',file:zip.split('/').at(-1),size:(await stat(zip)).size,sha256:await sha(zip),archiveSha256:encryption.archiveSha256,encryption:'AES-256-GCM; key excluded and sent only via SSH'};await writeFile(join(dir,'encrypted-bundle.json'),JSON.stringify(bundle,null,2),{mode:0o600});return bundle;
}
export async function uploadEncrypted(release,base=resolve('.release')){
 const dir=join(base,release),bundle=JSON.parse(await readFile(join(dir,'encrypted-bundle.json'),'utf8')),file=join(dir,bundle.file);if(await sha(file)!==bundle.sha256)throw Error('Encrypted ZIP changed');
 const config=storageSettings(process.env);if(config.provider!=='gcs'||config.endpoint!=='https://storage.googleapis.com')throw Error('Expected configured GCS XML endpoint');
 const {S3Client,PutObjectCommand,HeadObjectCommand}=await import('@aws-sdk/client-s3');const client=new S3Client({region:config.region,endpoint:config.endpoint,forcePathStyle:true,credentials:{accessKeyId:config.accessKeyId,secretAccessKey:config.secretAccessKey},maxAttempts:1,requestChecksumCalculation:'WHEN_REQUIRED',responseChecksumValidation:'WHEN_REQUIRED'});
 const key=`store-next/releases-encrypted/${release}/${bundle.sha256}/${bundle.file}`;
 try{await client.send(new PutObjectCommand({Bucket:config.bucket,Key:key,Body:createReadStream(file),ContentLength:bundle.size,ContentType:'application/zip',CacheControl:'private,no-store,max-age=0',Metadata:{sha256:bundle.sha256,encrypted:'aes-256-gcm'}}));const head=await client.send(new HeadObjectCommand({Bucket:config.bucket,Key:key}));if(head.Metadata?.sha256!==bundle.sha256||Number(head.ContentLength)!==bundle.size)throw Error('Uploaded ciphertext mismatch');
 const receipt={...bundle,bucket:config.bucket,key,url:`${config.endpoint}/${config.bucket}/${key}`,transport:'OSS ciphertext ZIP; SSH key side channel'};await writeFile(join(dir,'encrypted-upload.json'),JSON.stringify(receipt,null,2),{mode:0o600});return receipt;
 }finally{client.destroy();}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){const [action,release]=process.argv.slice(2);if(!['pack','upload'].includes(action))throw Error('Usage: encrypted-release-bundle.mjs pack|upload prod-YYYYMMDD-HHmmss');console.log(JSON.stringify(await (action==='pack'?packEncrypted(release):uploadEncrypted(release))));}
