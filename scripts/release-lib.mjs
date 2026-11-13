import { createHash } from 'node:crypto';
export function parseArgs(argv) {
  const args = argv.filter(x => x !== '--');
  const command = args.shift();
  if (!['build', 'deploy', 'rollback'].includes(command)) throw new Error('Expected build, deploy or rollback');
  const options = { command, env: 'local', assets: 'local', config: '.env.next.local' };
  const allowed = new Set(['env', 'assets', 'release', 'config', 'platform', 'reuse-staff']);
  while (args.length) {
    const key = args.shift()?.replace(/^--/, ''); const value = args.shift();
    if (!allowed.has(key) || !value || value.startsWith('--')) throw new Error('Invalid release arguments');
    options[key] = value;
  }
  if (options.env !== 'local') throw new Error('Production deployment remains disabled until P7 acceptance');
  if (!['local', 'cdn'].includes(options.assets)) throw new Error('assets must be local or cdn');
  if (options.release && !/^[a-z0-9][a-z0-9-]{0,62}$/.test(options.release)) throw new Error('Invalid release ID');
  if (command !== 'build' && !options.release) throw new Error('--release is required');
  if (options.platform && !/^linux\/(arm64|amd64)$/.test(options.platform)) throw new Error('Unsupported platform');
  if(options['reuse-staff']&&(!/^[a-z0-9][a-z0-9-]{0,62}$/.test(options['reuse-staff'])||command!=='build'))throw new Error('--reuse-staff requires build and an existing release ID');
  return options;
}
export function digest(value) { return createHash('sha256').update(value).digest('hex'); }
export function assetBase(release, app, mode, env) {
  if (mode === 'local') return `/r/${release}/${app}/`;
  const base = new URL(env.NEXT_CDN_BASE_URL || env.STORE_FILES_CDN_BASE || '');
  if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash) throw new Error('NEXT_CDN_BASE_URL must be a public HTTPS base');
  const prefix = (env.NEXT_OSS_PREFIX ?? 'store-next').replace(/^\/+|\/+$/g, '');
  if (!prefix || prefix.split('/').some(x => x === '..' || x === '.')) throw new Error('Invalid OSS prefix');
  return new URL(`${prefix}/${release}/${app}/`, base.href.replace(/\/?$/, '/')).href;
}
export function gatewayConfig(release) {
  if (!/^[a-z0-9-]+$/.test(release)) throw new Error('Invalid release');
  return `server {
 listen 80; server_name _; root /usr/share/nginx/html;
 absolute_redirect off;
 resolver 127.0.0.11 valid=10s ipv6=off;
 add_header X-Store-Release "${release}" always;
 add_header X-Content-Type-Options nosniff always;
 add_header X-Frame-Options DENY always;
 add_header Referrer-Policy strict-origin-when-cross-origin always;
 add_header Strict-Transport-Security "max-age=31536000" always;
 add_header Content-Security-Policy "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" always;
 gzip on; gzip_types text/css application/javascript application/json image/svg+xml;
 location ^~ /api/ {
   set $api_upstream http://api:3000;
   proxy_pass $api_upstream; proxy_http_version 1.1;
   proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto $scheme;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_connect_timeout 3s; proxy_read_timeout 30s;
   client_max_body_size 51m;
 }
 # Direct base-zone/grid execution was retired before the React migration.
 location ~ ^/my/work/(zones/[0-9]+|grids/[0-9]+(?:/(?:assign|visit))?)/?$ {
   set $legacy_upstream http://api:3000;
   rewrite ^/my/work/(.*?)/?$ /api/legacy-work/$1 break;
   proxy_pass $legacy_upstream; proxy_http_version 1.1;
   proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto $scheme;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_connect_timeout 3s; proxy_read_timeout 30s; client_max_body_size 64k;
 }
 location = /zone-tasks {
   if ($request_method !~ ^(GET|HEAD)$) { return 405; }
   return 308 /admin/zone-tasks$is_args$args;
 }
 location ~ ^/zone-tasks/(?<legacy_batch_id>[0-9]+)/?$ {
   if ($request_method !~ ^(GET|HEAD)$) { return 405; }
   return 308 /admin/zone-tasks/$legacy_batch_id$is_args$args;
 }
 location = /merchant-map {
   if ($request_method !~ ^(GET|HEAD)$) { return 405; }
   return 308 /admin/merchant-map$is_args$args;
 }
 location ~ ^/merchant-map/(data|detail)$ {
   set $merchant_upstream http://api:3000;
   rewrite ^/merchant-map/(.*)$ /api/merchant-map/$1 break;
   proxy_pass $merchant_upstream; proxy_http_version 1.1;
   proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto $scheme;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_connect_timeout 3s; proxy_read_timeout 10s;
 }
 location = /logs { if ($request_method !~ ^(GET|HEAD)$) { return 405; } return 308 /admin/logs$is_args$args; }
 location = /zones {
   if ($request_method !~ ^(GET|HEAD)$) { return 405; }
   return 308 /admin/zones$is_args$args;
 }
 location = /zones/manage {
   if ($request_method !~ ^(GET|HEAD)$) { return 405; }
   return 308 /admin/zones$is_args$args;
 }
 location ^~ /r/ { try_files $uri =404; add_header X-Content-Type-Options nosniff always;
 add_header X-Frame-Options DENY always;
 add_header Referrer-Policy strict-origin-when-cross-origin always;
 add_header Strict-Transport-Security "max-age=31536000" always;
 add_header Content-Security-Policy "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" always; add_header Cache-Control "public,max-age=31536000,immutable" always; add_header X-Store-Release "${release}" always; }
 location = /mcp {
   set $protocol_upstream http://api:3000;
   proxy_pass $protocol_upstream; proxy_http_version 1.1;
   proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto $scheme;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_connect_timeout 3s; proxy_read_timeout 300s; proxy_send_timeout 300s;
   proxy_buffering off; client_max_body_size 25m;
 }
 location ^~ /mcp/ {
   set $protocol_upstream http://api:3000;
   proxy_pass $protocol_upstream; proxy_http_version 1.1;
   proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto $scheme;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_connect_timeout 3s; proxy_read_timeout 300s; proxy_send_timeout 300s;
   proxy_buffering off; client_max_body_size 25m;
 }
 location ^~ /oauth/ {
   set $protocol_upstream http://api:3000;
   proxy_pass $protocol_upstream; proxy_http_version 1.1;
   proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto $scheme;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_connect_timeout 3s; proxy_read_timeout 300s; proxy_send_timeout 300s;
   proxy_buffering off; client_max_body_size 25m;
 }
 location = /.well-known/oauth-authorization-server {
   set $protocol_upstream http://api:3000;
   proxy_pass $protocol_upstream; proxy_http_version 1.1;
   proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto $scheme;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_connect_timeout 3s; proxy_read_timeout 300s; proxy_send_timeout 300s;
   proxy_buffering off; client_max_body_size 25m;
 }
 location = /.well-known/oauth-protected-resource {
   set $protocol_upstream http://api:3000;
   proxy_pass $protocol_upstream; proxy_http_version 1.1;
   proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto $scheme;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_connect_timeout 3s; proxy_read_timeout 300s; proxy_send_timeout 300s;
   proxy_buffering off; client_max_body_size 25m;
 }
 location ^~ /mcp { default_type application/json; return 404 '{"code":"NOT_FOUND"}'; }
 location = /oauth { default_type application/json; return 404 '{"code":"NOT_FOUND"}'; }
 location ^~ /.well-known/ { default_type application/json; return 501 '{"code":"MIGRATION_PENDING"}'; }
 location = /admin { return 308 /admin/; }
 location /admin/ { try_files $uri /admin/index.html; add_header X-Content-Type-Options nosniff always;
 add_header X-Frame-Options DENY always;
 add_header Referrer-Policy strict-origin-when-cross-origin always;
 add_header Strict-Transport-Security "max-age=31536000" always;
 add_header Content-Security-Policy "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" always; add_header Cache-Control no-cache always; add_header X-Store-Release "${release}" always; }
 location ~ \\.(?:js|css|woff2?|png|jpe?g|svg|webp|map)$ { return 404; }
 location / { try_files $uri /index.html; add_header X-Content-Type-Options nosniff always;
 add_header X-Frame-Options DENY always;
 add_header Referrer-Policy strict-origin-when-cross-origin always;
 add_header Strict-Transport-Security "max-age=31536000" always;
 add_header Content-Security-Policy "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" always; add_header Cache-Control no-cache always; add_header X-Store-Release "${release}" always; }
}`;
}
export function edgeConfig(release) {
  if (!/^[a-z0-9-]+$/.test(release)) throw new Error('Invalid release');
  return `server { listen 80; server_name _;
    resolver 127.0.0.11 valid=5s ipv6=off;
    location ~ ^/(mcp(/|$)|oauth/|[.]well-known/oauth-(authorization-server|protected-resource)$) {
      set $protocol_candidate http://next-${release}:80;
      proxy_pass $protocol_candidate; proxy_http_version 1.1;
      proxy_set_header Host $http_host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme; proxy_connect_timeout 3s;
      proxy_read_timeout 300s; proxy_send_timeout 300s; proxy_buffering off; client_max_body_size 25m;
    }
    location / { set $candidate http://next-${release}:80; proxy_pass $candidate; proxy_http_version 1.1;
      proxy_set_header Host $http_host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme; proxy_connect_timeout 3s; proxy_read_timeout 35s;
    }
  }`;
}

/** Run inside the target image with the shared job volume mounted read-only. No payload logging. */
export function taskCompatibilityProbe(supportedKinds) {
  if (!Array.isArray(supportedKinds) || supportedKinds.some(kind => !/^[a-z][a-z0-9.]{1,80}$/.test(kind))) throw new Error('Invalid task capability list');
  return `const fs=require('fs'),path=require('path');const supported=new Set(${JSON.stringify(supportedKinds)});const kinds=new Set();for(const state of ['queued','running']){const dir='/data/jobs/'+state;if(!fs.existsSync(dir))continue;for(const name of fs.readdirSync(dir)){if(!name.endsWith('.json'))continue;const task=JSON.parse(fs.readFileSync(path.join(dir,name),'utf8'));if(!supported.has(task.kind))kinds.add(String(task.kind));}}if(kinds.size){console.error('Unsupported pending task types: '+[...kinds].sort().join(', '));process.exit(2);}`;
}

export function retiredReleaseNetworks(networks,knownReleases,keepReleases){
 const known=new Set(knownReleases),keep=new Set(keepReleases.filter(Boolean));
 return networks.filter(network=>{
  const name=network.Name??'',match=/^store-next-([a-z0-9-]+)_private$/.exec(name);if(!match)return false;
  const id=match[1];return known.has(id)&&!keep.has(id)&&network.Driver==='bridge'&&network.Scope==='local'&&network.Labels?.['com.docker.compose.project']==='store-next-'+id&&network.Labels?.['com.docker.compose.network']==='private'&&network.Containers&&Object.keys(network.Containers).length===0;
 }).map(network=>({id:network.Id,name:network.Name}));
}

/** Local Docker may explicitly target the restored baseline; dev/test URLs remain independent. */
export function localDeploymentDatabase(env){
 const explicit=env.NEXT_DOCKER_DATABASE_URL;
 let url;try{url=new URL(explicit||env.NEXT_DATABASE_URL||'');}catch{throw Error('Local Docker database configuration is required');}
 if(!['postgres:','postgresql:'].includes(url.protocol)||!['127.0.0.1','localhost','host.docker.internal','[::1]'].includes(url.hostname)||!url.username)throw Error('Docker deployment requires a local PostgreSQL database');
 const name=decodeURIComponent(url.pathname.slice(1));
 if(name!=='store_settle_refactor_p1'&&!(explicit&&name==='store_settle'))throw Error('Baseline requires explicit NEXT_DOCKER_DATABASE_URL');
 if(['127.0.0.1','localhost','[::1]'].includes(url.hostname))url.hostname='host.docker.internal';
 return {url,jobsVolume:name==='store_settle'?'store-next-jobs-store-settle':'store-next-jobs',baseline:name==='store_settle'};
}

/** OAuth origins must name the actual local gateway, never Docker's internal api host. */
export function localMcpEnvironment(){
 return {NEXT_MCP_PUBLIC_URL:'http://127.0.0.1:8000/mcp',NEXT_MCP_AUTHORIZATION_UI_URL:'http://127.0.0.1:8000/admin/mcp-authorize'};
}

/** Broad source snapshots may be exercised in isolation but must not become a traffic switch by accident. */
export function assertDeployableManifest(manifest){
 if(manifest.acceptanceOnly)throw new Error('This release is an isolated acceptance snapshot; prepare a scoped deployment candidate before switching local traffic');
}
