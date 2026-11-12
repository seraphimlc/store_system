import test from 'node:test';import assert from 'node:assert/strict';import {storageSettings} from './asset-storage.mjs';import {assetBase} from './release-lib.mjs';
test('existing STORE_FILES aliases support GCS HMAC with an empty endpoint without affecting the Aliyun contract',()=>{
 const env={STORE_FILES_OSS_BUCKET:'visitworld-assets',STORE_FILES_OSS_ENDPOINT:'',STORE_FILES_CDN_BASE:'https://cdn.visitworld.me',OSS_ACCESS_KEY_ID:'fixture',OSS_ACCESS_KEY_SECRET:'unused'};
 assert.deepEqual(storageSettings(env),{provider:'gcs',endpoint:'https://storage.googleapis.com',region:'asia-northeast1',bucket:'visitworld-assets',accessKeyId:'fixture',secretAccessKey:'unused'});assert.equal(assetBase('release-a','staff','cdn',env),'https://cdn.visitworld.me/store-next/release-a/staff/');
 assert.equal(storageSettings({NEXT_OSS_ENDPOINT:'oss-cn-hangzhou.aliyuncs.com',NEXT_OSS_REGION:'oss-cn-hangzhou'}).provider,'aliyun');assert.equal(storageSettings({...env,NEXT_OSS_PROVIDER:'gcs',NEXT_OSS_ENDPOINT:'storage.googleapis.com'}).endpoint,'https://storage.googleapis.com');assert.throws(()=>storageSettings({NEXT_OSS_PROVIDER:'unknown'}));
});

test('GCS native CORS grants public asset reads only and matches the gcloud configuration',async()=>{
 const {publicAssetCorsXml}=await import('./gcs-cors.mjs'),{readFile}=await import('node:fs/promises');const xml=publicAssetCorsXml(),json=JSON.parse(await readFile('deploy-next/gcs-cors.json','utf8'));
 assert.ok(xml.includes('<CorsConfig>'));assert.ok(xml.includes('<Origin>*</Origin>'));assert.ok(xml.includes('<Method>GET</Method>'));assert.ok(xml.includes('<Method>HEAD</Method>'));assert.doesNotMatch(xml,/<Method>(?:PUT|POST|DELETE)<\/Method>|AllowCredentials|AccessKey/);assert.deepEqual(json[0].method,['GET','HEAD']);assert.deepEqual(json[0].origin,['*']);assert.equal(json[0].maxAgeSeconds,3600);
});

test('GCS CORS signs only the official bucket endpoint and reports permission denial without credential details',async()=>{
 const {gcsCorsRequest,publicAssetCorsXml}=await import('./gcs-cors.mjs');const env={STORE_FILES_OSS_BUCKET:'fixture-public-assets',OSS_ACCESS_KEY_ID:'fixture-key',OSS_ACCESS_KEY_SECRET:'fixture-secret'};let called=false;
 const result=await gcsCorsRequest('PUT',env,publicAssetCorsXml(),async(url,request)=>{called=true;assert.equal(url,'https://storage.googleapis.com/fixture-public-assets?cors');assert.equal(request.method,'PUT');assert.equal(request.body,publicAssetCorsXml());assert.ok(request.headers.authorization.startsWith('AWS4-HMAC-SHA256 '));assert.equal(request.headers['content-type'],'application/xml');return new Response('<Error><Code>AccessDenied</Code><Message>credential info</Message></Error>',{status:403});});
 assert.equal(called,true);assert.deepEqual(result,{status:403,ok:false,code:'AccessDenied'});assert.doesNotMatch(JSON.stringify(result),/fixture-secret|credential info/);await assert.rejects(gcsCorsRequest('DELETE',env));await assert.rejects(gcsCorsRequest('PUT',{...env,NEXT_OSS_ENDPOINT:'https://other.invalid'},publicAssetCorsXml()));
});
