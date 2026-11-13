/** Read-only verification; never changes IAM, keys, production objects or CDN caches. */
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
export function mapsRestrictionsValid(value,origin,services){
 const r=value?.restrictions,expected=new URL(origin).origin+'/*';
 const referrers=r?.browserKeyRestrictions?.allowedReferrers;
 return Array.isArray(referrers)&&referrers.length>0&&referrers.every(ref=>ref===expected)&&Array.isArray(r?.apiTargets)&&r.apiTargets.length>0&&r.apiTargets.every(target=>services.includes(target.service));
}
export async function anonymousObjectBlocked(url,request=fetch){
 const value=new URL(url);if(value.protocol!=='https:'||value.username||value.password)throw Error('HTTPS object URL required');
 const response=await request(value,{headers:{Range:'bytes=0-0'},redirect:'manual',signal:AbortSignal.timeout(15000)});await response.body?.cancel();
 // A missing object is closed too. Other statuses need investigation and never count as success.
 return [401,403,404].includes(response.status);
}
function json(args){const r=spawnSync('gcloud',[...args,'--format=json'],{encoding:'utf8',timeout:30000});if(r.status!==0)throw Error('gcloud read failed');return JSON.parse(r.stdout);}
async function main(){
 const bucket=process.env.NEXT_RELEASE_BUCKET,origin=process.env.NEXT_PUBLIC_ORIGIN,project=process.env.GOOGLE_CLOUD_PROJECT,keyId=process.env.NEXT_MAPS_KEY_RESOURCE,services=(process.env.NEXT_MAPS_ALLOWED_SERVICES||'').split(',').filter(Boolean);
 if(!bucket||!origin||!project||!keyId||!services.length)throw Error('Set NEXT_RELEASE_BUCKET, NEXT_PUBLIC_ORIGIN, GOOGLE_CLOUD_PROJECT, NEXT_MAPS_KEY_RESOURCE and NEXT_MAPS_ALLOWED_SERVICES');
 const b=json(['storage','buckets','describe',`gs://${bucket}`]);
 const k=json(['services','api-keys','describe',keyId,'--project',project]);
 const checks={privateReleaseBucket:b.public_access_prevention==='enforced'||b.iamConfiguration?.publicAccessPrevention==='enforced',mapsRestrictions:mapsRestrictionsValid(k,origin,services)};
 if(process.env.NEXT_LEGACY_RELEASE_URL)checks.legacyReleaseClosed=await anonymousObjectBlocked(process.env.NEXT_LEGACY_RELEASE_URL);
 console.log(JSON.stringify(checks));if(Object.values(checks).some(v=>!v))process.exitCode=1;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)main().catch(()=>{console.error('Cloud security verification incomplete; check explicit configuration and gcloud read permissions.');process.exitCode=1;});
