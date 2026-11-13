import test from 'node:test';
import assert from 'node:assert/strict';
import { parseArgs, assetBase, gatewayConfig, edgeConfig, digest, taskCompatibilityProbe } from './release-lib.mjs';
test('release arguments prevent target confusion and path traversal', () => {
  assert.equal(parseArgs(['deploy','--','--release','r-123']).release,'r-123');
  assert.throws(()=>parseArgs(['deploy','--release','../../production']));
  assert.throws(()=>parseArgs(['deploy','--release','r1','--env','production']),/P7/);
  assert.throws(()=>parseArgs(['rollback']),/required/);
  assert.throws(()=>parseArgs(['build','--assets','remote']),/assets/);
  assert.equal(parseArgs(['build','--reuse-staff','old-release'])['reuse-staff'],'old-release');
  assert.throws(()=>parseArgs(['build','--reuse-staff','../old']));assert.throws(()=>parseArgs(['deploy','--release','r1','--reuse-staff','old']));
});
test('resource base is independent from the application route and frozen by release', () => {
  assert.equal(assetBase('r1','admin','local',{}),'/r/r1/admin/');
  assert.equal(assetBase('r1','staff','cdn',{NEXT_CDN_BASE_URL:'https://cdn.example.com/',NEXT_OSS_PREFIX:'assets'}),'https://cdn.example.com/assets/r1/staff/');
  assert.throws(()=>assetBase('r1','admin','cdn',{NEXT_CDN_BASE_URL:'http://cdn.example.com'}),/HTTPS/);
  assert.throws(()=>assetBase('r1','admin','cdn',{NEXT_CDN_BASE_URL:'https://cdn.example.com',NEXT_OSS_PREFIX:'../x'}),/prefix/);
});
test('gateway isolates API, missing assets and two SPA roots', () => {
  const config=gatewayConfig('r1');
  assert.match(config,/location \^~ \/api\//);
  assert.match(config,/location \^~ \/r\/.*try_files \$uri =404/s);
  assert.match(config,/try_files \$uri \/admin\/index.html/);
  assert.match(config,/try_files \$uri \/index.html/);
  assert.match(config,/Cache-Control no-cache/);
  assert.match(edgeConfig('r1'),/http:\/\/next-r1:80/);
  assert.throws(()=>edgeConfig('r1; malicious'));
  assert.equal(digest('same'),digest(Buffer.from('same')));
});

test('rollback task compatibility checks pending work without printing its private payload',()=>{
 const probe=taskCompatibilityProbe(['system.check','report.analysis']);
 assert.match(probe,/queued/);assert.match(probe,/running/);assert.match(probe,/process.exit\(2\)/);
 assert.doesNotMatch(probe,/task.payload/);assert.throws(()=>taskCompatibilityProbe(['bad;injection']));
});

test('network reclamation protects active endpoints, current/previous/target, unmanaged releases and other projects',async()=>{
 const {retiredReleaseNetworks}=await import('./release-lib.mjs');
 const network=(id,extra={})=>({Id:'id-'+id,Name:'store-next-'+id+'_private',Driver:'bridge',Scope:'local',Labels:{'com.docker.compose.project':'store-next-'+id,'com.docker.compose.network':'private'},Containers:{},...extra});
 const all=[network('retired'),network('current'),network('previous'),network('target'),network('active',{Containers:{abc:{Name:'running'}}}),network('unmanaged'),network('wrong-label',{Labels:{'com.docker.compose.project':'other'}}),network('missing-state',{Containers:undefined}),network('other',{Name:'other_private'}),network('overlay',{Driver:'overlay'})];
 assert.deepEqual(retiredReleaseNetworks(all,['retired','current','previous','target','active','wrong-label','missing-state','other','overlay'],['current','previous','target']),[{id:'id-retired',name:'store-next-retired_private'}]);
});

test('legacy direct execution is proxied internally, read aliases preserve queries and never consume old POST forms',()=>{
 const config=gatewayConfig('legacy-test');
 assert.match(config,/location ~ \^\/my\/work\/\(zones\/\[0-9\]\+\|grids\/\[0-9\]\+/);
 assert.match(config,/rewrite \^\/my\/work\/\(\.\*\?\)\/\?\$ \/api\/legacy-work\/\$1 break/);
 assert.match(config,/proxy_pass \$legacy_upstream/);
 assert.ok(config.indexOf('set $legacy_upstream')<config.indexOf('rewrite ^/my/work/'));
 assert.match(config,/return 308 \/admin\/zone-tasks\$is_args\$args/);
 assert.match(config,/absolute_redirect off/);
 assert.ok(config.includes('(?<legacy_batch_id>[0-9]+)'));
 assert.ok(config.includes('/admin/zone-tasks/$legacy_batch_id$is_args$args'));
 assert.match(config,/return 308 \/admin\/zones\$is_args\$args/);
 assert.match(config,/return 308 \/admin\/logs\$is_args\$args/);
 assert.match(config,/if \(\$request_method !~ \^\(GET\|HEAD\)\$\) \{ return 405; \}/);
 assert.doesNotMatch(config,/rewrite.*batch-grids/);
});

test('MCP/OAuth gateway forwards native paths, original host port and long calls without SPA fallback',()=>{
 const config=gatewayConfig('mcp-local-test');
 for(const route of ['location = /mcp {','location ^~ /mcp/ {','location ^~ /oauth/ {','location = /.well-known/oauth-authorization-server {','location = /.well-known/oauth-protected-resource {']){
  const start=config.indexOf(route);assert.ok(start>=0,route);const block=config.slice(start,config.indexOf('}',start)+1);
  assert.ok(block.includes('proxy_pass $protocol_upstream'));assert.ok(block.includes('proxy_set_header Host $http_host'));
  assert.ok(block.includes('proxy_read_timeout 300s'));assert.ok(block.includes('proxy_buffering off'));assert.ok(block.includes('client_max_body_size 25m'));assert.ok(!block.includes('try_files'));
 }
 assert.ok(config.includes('location ^~ /mcp { default_type application/json; return 404'));
 assert.doesNotMatch(config,/MCP_NOT_SUPPORTED|OAUTH_NOT_SUPPORTED|rewrite.*api\/mcp/);
});

test('local OAuth environment uses the same gateway origin as the connector',async()=>{
 const {localMcpEnvironment}=await import('./release-lib.mjs');assert.deepEqual(localMcpEnvironment(),{NEXT_MCP_PUBLIC_URL:'http://127.0.0.1:8000/mcp',NEXT_MCP_AUTHORIZATION_UI_URL:'http://127.0.0.1:8000/admin/mcp-authorize'});
});

test('Docker baseline is explicit, local and separated from development and task queues',async()=>{
 const {localDeploymentDatabase}=await import('./release-lib.mjs');
 const dev='postgresql://tester:fixture@127.0.0.1:5433/store_settle_refactor_p1';const baseline='postgresql://tester:fixture@127.0.0.1:5433/store_settle';
 const env={NEXT_DATABASE_URL:dev,NEXT_DOCKER_DATABASE_URL:baseline};const selected=localDeploymentDatabase(env);
 assert.equal(selected.url.pathname,'/store_settle');assert.equal(selected.url.hostname,'host.docker.internal');assert.equal(selected.jobsVolume,'store-next-jobs-store-settle');assert.equal(selected.baseline,true);assert.equal(env.NEXT_DATABASE_URL,dev);
 assert.equal(localDeploymentDatabase({NEXT_DATABASE_URL:dev}).jobsVolume,'store-next-jobs');
 assert.throws(()=>localDeploymentDatabase({NEXT_DATABASE_URL:baseline}));assert.throws(()=>localDeploymentDatabase({NEXT_DOCKER_DATABASE_URL:baseline.replace('127.0.0.1','production.example')}));assert.throws(()=>localDeploymentDatabase({NEXT_DOCKER_DATABASE_URL:baseline.replace('/store_settle','/postgres')}));
});

test('entry proxy also retains MCP/OAuth port, body limit and long-call behavior',()=>{const config=edgeConfig('mcp-entry');assert.match(config,/location ~ \^\/\(mcp/);assert.match(config,/proxy_set_header Host \$http_host/);assert.ok(config.includes('proxy_read_timeout 300s'));assert.ok(config.includes('client_max_body_size 25m'));assert.ok(config.includes('proxy_buffering off'));});

test('isolated acceptance snapshots cannot accidentally replace current local traffic',async()=>{
 const {assertDeployableManifest}=await import('./release-lib.mjs');
 assert.throws(()=>assertDeployableManifest({acceptanceOnly:true}),/isolated acceptance/);
 assert.doesNotThrow(()=>assertDeployableManifest({id:'reviewed-release'}));
});
