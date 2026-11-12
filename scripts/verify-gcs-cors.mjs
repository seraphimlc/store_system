import {mkdir,writeFile} from 'node:fs/promises';import {gcsCorsRequest,publicAssetCorsXml} from './gcs-cors.mjs';
const apply=process.argv.includes('--apply');if(process.argv.slice(2).some(arg=>arg!=='--apply'))throw Error('Use [--apply]');
const before=await gcsCorsRequest('GET',process.env);const result={capturedAt:new Date().toISOString(),before:{status:before.status,code:before.code},applied:false};
await mkdir('data/refactor',{recursive:true});await writeFile('data/refactor/gcs-cors-public-assets.xml',publicAssetCorsXml(),{mode:0o600});
if(apply){if(before.ok)await writeFile('data/refactor/gcs-cors-before.xml',before.xml,{mode:0o600});const after=await gcsCorsRequest('PUT',process.env,publicAssetCorsXml());result.update={status:after.status,code:after.code};result.applied=after.ok;if(!after.ok)process.exitCode=1;}
await writeFile('data/refactor/gcs-cors-verification.json',JSON.stringify(result,null,2),{mode:0o600});console.log(JSON.stringify(result));
