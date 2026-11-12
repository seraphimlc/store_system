/** Deployment-only public assets. Private uploads never pass through this adapter. */
import {readFile} from 'node:fs/promises';
export function storageSettings(env){
 const provider=env.NEXT_OSS_PROVIDER??(!env.NEXT_OSS_ENDPOINT&&!env.STORE_FILES_OSS_ENDPOINT&&env.STORE_FILES_OSS_BUCKET?'gcs':'aliyun');
 if(!['gcs','aliyun'].includes(provider))throw Error('Unsupported asset storage provider');
 const endpoint=env.NEXT_OSS_ENDPOINT||env.STORE_FILES_OSS_ENDPOINT||(provider==='gcs'?'https://storage.googleapis.com':'');
 return {provider,endpoint:endpoint&&!endpoint.startsWith('https://')?'https://'+endpoint:endpoint,region:env.NEXT_OSS_REGION||(provider==='gcs'?'asia-northeast1':''),bucket:env.NEXT_OSS_BUCKET||env.STORE_FILES_OSS_BUCKET,accessKeyId:env.NEXT_OSS_ACCESS_KEY_ID||env.OSS_ACCESS_KEY_ID,secretAccessKey:env.NEXT_OSS_ACCESS_KEY_SECRET||env.OSS_ACCESS_KEY_SECRET};
}
export async function assetStorage(env){
 const config=storageSettings(env);if(Object.values(config).some(value=>!value))throw Error('Asset bucket/endpoint/region/HMAC credentials are required');
 if(config.provider==='gcs'){
  const {S3Client,HeadObjectCommand,PutObjectCommand}=await import('@aws-sdk/client-s3');
  const client=new S3Client({region:config.region,endpoint:config.endpoint,forcePathStyle:true,credentials:{accessKeyId:config.accessKeyId,secretAccessKey:config.secretAccessKey},requestChecksumCalculation:'WHEN_REQUIRED',responseChecksumValidation:'WHEN_REQUIRED',maxAttempts:2});
  return {async publish(key,file,asset,mime){let existing;try{existing=await client.send(new HeadObjectCommand({Bucket:config.bucket,Key:key}));}catch(error){if(error.$metadata?.httpStatusCode!==404&&error.name!=='NotFound')throw Error('GCS asset metadata read failed');}
   if(existing){if(existing.Metadata?.sha256!==asset.sha256||Number(existing.ContentLength)!==asset.size)throw Error('Existing immutable GCS object differs');return;}
   try{await client.send(new PutObjectCommand({Bucket:config.bucket,Key:key,Body:await readFile(file),ContentType:mime,CacheControl:'public,max-age=31536000,immutable',Metadata:{sha256:asset.sha256}}));}catch{throw Error('GCS asset upload failed');}
  },close(){client.destroy();}};
 }
 const {default:OSS}=await import('ali-oss');const client=new OSS({endpoint:config.endpoint,region:config.region,bucket:config.bucket,accessKeyId:config.accessKeyId,accessKeySecret:config.secretAccessKey,stsToken:env.NEXT_OSS_STS_TOKEN,secure:true,timeout:60000});
 return {async publish(key,file,asset,mime){let existing;try{existing=await client.head(key);}catch(error){if(error.status!==404&&error.code!=='NoSuchKey')throw Error('OSS metadata read failed');}if(existing&&(existing.res.headers['x-oss-meta-sha256']!==asset.sha256||Number(existing.res.headers['content-length'])!==asset.size))throw Error('Existing immutable OSS object differs');if(!existing)await client.put(key,await readFile(file),{headers:{'Content-Type':mime,'Cache-Control':'public,max-age=31536000,immutable','x-oss-meta-sha256':asset.sha256,'x-oss-forbid-overwrite':'true'}});},close(){}};
}
