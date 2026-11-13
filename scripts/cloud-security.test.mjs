import test from 'node:test';import assert from 'node:assert/strict';
import {mapsRestrictionsValid,anonymousObjectBlocked} from './cloud-security.mjs';
test('Maps restrictions require exact production origin and an API allowlist',()=>{
 const restrictions={browserKeyRestrictions:{allowedReferrers:['https://store.fixture.test/*']},apiTargets:[{service:'maps-backend.googleapis.com'}]},allowed=['maps-backend.googleapis.com'];
 assert.equal(mapsRestrictionsValid({restrictions},'https://store.fixture.test',allowed),true);
 assert.equal(mapsRestrictionsValid({restrictions:{...restrictions,browserKeyRestrictions:{allowedReferrers:['*']}}},'https://store.fixture.test',allowed),false);
 assert.equal(mapsRestrictionsValid({restrictions},'https://other.fixture.test',allowed),false);
 assert.equal(mapsRestrictionsValid({restrictions},'https://store.fixture.test',[]),false);
});
test('public release checks never accept anonymous success or redirects',async()=>{
 for(const status of [200,206,302,403,404,500]){let cancelled=false;const result=await anonymousObjectBlocked('https://cdn.fixture.test/archive',async(_url,options)=>{assert.equal(options.headers.Range,'bytes=0-0');assert.equal(options.redirect,'manual');return {status,body:{cancel:async()=>{cancelled=true;}}};});assert.equal(result,[403,404].includes(status));assert.equal(cancelled,true);}
});
