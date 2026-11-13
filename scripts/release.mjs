import {replaceGateway,handoffRelease} from './release-transition.mjs';
import {assetStorage} from './asset-storage.mjs';
import { spawn, spawnSync } from 'node:child_process';
import { readFile, writeFile, mkdir, cp, readdir, rename, rm, access, stat } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { resolve, join, relative, isAbsolute } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { createGzip } from 'node:zlib';
import { parseArgs, localDeploymentDatabase, digest, assetBase, gatewayConfig, edgeConfig, taskCompatibilityProbe, retiredReleaseNetworks, localMcpEnvironment, assertDeployableManifest } from './release-lib.mjs';
const root = process.cwd();
const base = resolve(root, '.release');
const opts = parseArgs(process.argv.slice(2));
let stage = 'preflight';
function progress(name) { stage = name; console.log(`[release] ${name}`); }
async function exists(path) { try { await access(path); return true; } catch { return false; } }
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, encoding: 'utf8', stdio: options.quiet ? 'pipe' : 'inherit', ...options });
  if (result.error || result.status !== 0) throw new Error(`${command} failed at ${stage} (exit ${result.status ?? 'unavailable'})`);
  return result.stdout?.trim() ?? '';
}
async function json(path) { return JSON.parse(await readFile(path, 'utf8')); }
async function writeJson(path, value) { await writeFile(path, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 }); }
async function files(path) {
  const out = [];
  for (const dirent of await readdir(path, { withFileTypes: true })) {
    const target = join(path, dirent.name);
    if (dirent.isSymbolicLink()) throw new Error('Release files cannot contain symlinks');
    if (dirent.isDirectory()) out.push(...await files(target)); else if (dirent.isFile()) out.push(target);
  }
  return out;
}
async function imageId(tag) { return run('docker', ['image', 'inspect', '--format', '{{.Id}}', tag], { quiet: true }); }
async function archiveImages(tags, path) {
  const child = spawn('docker', ['image', 'save', ...tags], { stdio: ['ignore', 'pipe', 'inherit'] });
  const exit = new Promise((res, rej) => { child.on('error', rej); child.on('exit', code => code === 0 ? res() : rej(new Error('Image archive failed'))); });
  await Promise.all([pipeline(child.stdout, createGzip(), createWriteStream(path, { mode: 0o600 })), exit]);
}
async function build() {
  const commit = run('git', ['rev-parse', 'HEAD'], { quiet: true });
  const dirty = !!run('git', ['status', '--porcelain'], { quiet: true });
  const id = opts.release ?? `${new Date().toISOString().replace(/[^0-9]/g, '').slice(0,14)}-${commit.slice(0,8)}${dirty ? '-dirty' : ''}`;
  const dir = join(base, id);
  if (await exists(dir)) throw new Error('Release ID already exists; build a new immutable release');
  await mkdir(dir, { recursive: true });
  await writeJson(join(dir, 'building.json'), { commit, dirty });
  progress('applicable tests and type checks');
  run('pnpm', ['install', '--frozen-lockfile']);
  run('pnpm', ['test']);
  run('pnpm', ['typecheck']);
  progress('compile server, worker and contracts');
  for (const name of ['server', 'worker']) run('pnpm', ['--filter', `@store/${name}`, 'build']);
  run('pnpm', ['--filter', '@store/contracts', 'build']);
  const gateway = join(dir, 'gateway-context'); const api = join(dir, 'api-context');
  await mkdir(join(gateway, 'html', 'admin'), { recursive: true });
  const assets = [];
  let reusedStaff=null;
  if(opts['reuse-staff']){
    const previousDir=join(base,opts['reuse-staff']),previous=await json(join(previousDir,'manifest.json'));
    if(previous.id!==opts['reuse-staff']||previous.env!=='local')throw new Error('Staff reuse must reference an existing local release');
    const htmlBytes=await readFile(join(previousDir,'gateway-context/html/index.html'));
    if(digest(htmlBytes)!==previous.html['index.html'])throw new Error('Previous staff HTML checksum mismatch');
    await writeFile(join(gateway,'html/index.html'),htmlBytes);
    const staffAssets=previous.assets.filter(asset=>asset.app==='staff');if(!staffAssets.length)throw new Error('Previous staff assets missing');
    for(const asset of staffAssets){const source=join(previousDir,'assets/staff',asset.path),bytes=await readFile(source);if(digest(bytes)!==asset.sha256||bytes.length!==asset.size)throw new Error('Previous staff asset checksum mismatch');const staged=join(dir,'assets/staff',asset.path);await mkdir(resolve(staged,'..'),{recursive:true});await writeFile(staged,bytes);assets.push(asset);}
    reusedStaff={release:previous.id,htmlSha256:digest(htmlBytes),assetCount:staffAssets.length};
  }
  for (const app of ['admin', 'staff']) {
    if(app==='staff'&&reusedStaff){progress('retain previous staff HTML/assets without rebuilding');continue;}
    progress(`build ${app} (${opts.assets} assets)`);
    const url = assetBase(id, app, opts.assets, process.env);
    run('pnpm', ['--filter', `@store/${app}`, 'build'], { env: { ...process.env, ASSET_BASE_URL: url } });
    const dist = resolve(root, 'apps', app, 'dist');
    await cp(join(dist, 'index.html'), join(gateway, 'html', app === 'admin' ? 'admin/index.html' : 'index.html'));
    for (const path of await files(dist)) {
      const name = relative(dist, path).replaceAll('\\', '/');
      if (name === 'index.html') continue;
      if (name.endsWith('.map')) throw new Error('Public source maps are forbidden');
      const content = await readFile(path);
      const staged = join(dir, 'assets', app, name);
      await mkdir(resolve(staged, '..'), { recursive: true }); await writeFile(staged, content);
      assets.push({ app, path: name, sha256: digest(content), size: content.length, url: new URL(name, opts.assets === 'local' ? 'http://localhost:8000' + url : url).href });
      if (opts.assets === 'local') {
        const local = join(gateway, 'html', 'r', id, app, name);
        await mkdir(resolve(local, '..'), { recursive: true }); await writeFile(local, content);
      }
    }
  }
  await writeFile(join(gateway, 'default.conf'), gatewayConfig(id));
  await cp('deploy-next/gateway.Dockerfile', join(gateway, 'Dockerfile'));
  await mkdir(join(api, 'apps', 'server'), { recursive: true });
  for (const name of ['package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml']) await cp(name, join(api, name));
  await cp('apps/server/package.json', join(api, 'apps/server/package.json'));
  await cp('apps/server/dist', join(api, 'apps/server/dist'), { recursive: true });
  await cp('apps/worker/dist', join(api, 'apps/worker/dist'), { recursive: true });
  const zoneSource=resolve(process.env.NEXT_ZONE_DATA_DIRECTORY??'data/bd_zones'),zoneEvidence=[];
  await mkdir(join(api,'zone-evidence'),{recursive:true});
  if(await exists(zoneSource))for(const name of (await readdir(zoneSource)).filter(name=>name.endsWith('.roads-v1.source.json')).sort()){
    const bytes=await readFile(join(zoneSource,name));if(bytes.length>64*1024*1024)throw new Error('Zone evidence too large');
    const document=JSON.parse(bytes.toString('utf8'));if(!Array.isArray(document.elements)||document.remark)throw new Error('Incomplete river evidence');
    await writeFile(join(api,'zone-evidence',name),bytes);zoneEvidence.push({path:name,sha256:digest(bytes),size:bytes.length});
  }
  await cp('deploy-next/api.Dockerfile', join(api, 'Dockerfile'));
  const arch = run('docker', ['info', '--format', '{{.Architecture}}'], { quiet: true });
  const platform = opts.platform ?? (['aarch64', 'arm64'].includes(arch) ? 'linux/arm64' : 'linux/amd64');
  const images = { api: `store-next-api:${id}`, gateway: `store-next-gateway:${id}` };
  progress('build local runtime images');
  for (const name of ['api', 'gateway']) run('docker', ['buildx', 'build', '--load', '--platform', platform, '-t', images[name], join(dir, `${name}-context`)]);
  const imageDigests = {};
  for (const [name, tag] of Object.entries(images)) imageDigests[name] = await imageId(tag);
  progress('archive immutable images');
  await archiveImages(Object.values(images), join(dir, 'images.tar.gz'));
  const html = {};
  for (const name of ['index.html', 'admin/index.html']) html[name] = digest(await readFile(join(gateway, 'html', name)));
  const manifest = { id, commit, dirty, createdAt: new Date().toISOString(), env: opts.env, assetsMode: opts.assets, platform,
    componentScope:{api:'rebuild',worker:'rebuild-shared-image',admin:'rebuild',gateway:'rebuild',staff:reusedStaff??'rebuild'},supportedTaskKinds:['system.check','report.analysis','dashboard.analysis','settlement.judge','settlement.finalize','settlement.rebuild','import.visit','import.delete','reconciliation.monthly','store.master','reconciliation.insight','store.ai'],zoneEvidence,lockSha256: digest(await readFile('pnpm-lock.yaml')), images, imageDigests, html, assets };
  await writeJson(join(dir, 'manifest.json'), manifest);
  await rm(join(dir, 'building.json'));
  console.log(JSON.stringify({ release: id, manifest: relative(root, join(dir, 'manifest.json')), deploy: `pnpm release:deploy -- --env local --release ${id}` }, null, 2));
}
const mimeTypes = { '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.woff': 'font/woff', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.json': 'application/json' };
function mime(path) { return Object.entries(mimeTypes).find(([extension]) => path.endsWith(extension))?.[1] ?? 'application/octet-stream'; }
async function verifyAssets(manifest, dir, upload) {
  for (const asset of manifest.assets) {
    const bytes = await readFile(join(dir, 'assets', asset.app, asset.path));
    if (digest(bytes) !== asset.sha256 || bytes.length !== asset.size) throw new Error('Local asset checksum mismatch');
  }
  if (manifest.assetsMode !== 'cdn') return;
  const client=upload?await assetStorage(process.env):null;
  try{
  for (const asset of manifest.assets) {
    const key = `${(process.env.NEXT_OSS_PREFIX ?? 'store-next').replace(/^\/+|\/+$/g, '')}/${manifest.id}/${asset.app}/${asset.path}`;
    if(client)await client.publish(key,join(dir,'assets',asset.app,asset.path),asset,mime(asset.path));
    const response = await fetch(asset.url, { headers: { Origin: process.env.NEXT_PUBLIC_ORIGIN ?? 'http://localhost:8000' }, signal: AbortSignal.timeout(15000) });
    if (!response.ok || digest(Buffer.from(await response.arrayBuffer())) !== asset.sha256) throw new Error('CDN resource unavailable or checksum mismatch');
    if (!response.headers.get('content-type')?.startsWith(mime(asset.path))) throw new Error('CDN MIME mismatch');
    const cors = response.headers.get('access-control-allow-origin');
    if (cors !== '*' && cors !== (process.env.NEXT_PUBLIC_ORIGIN ?? 'http://localhost:8000')) throw new Error('CDN CORS must allow the application origin');
  }
  }finally{client?.close();}
}
async function ensureEdge() {
  const edge = join(base, 'edge'); await mkdir(edge, { recursive: true });
  const network = spawnSync('docker', ['network', 'inspect', 'store-next-entry'], { stdio: 'ignore' });
  if (network.status !== 0) run('docker', ['network', 'create', 'store-next-entry']);
  const config = join(edge, 'default.conf');
  if (!await exists(config)) await writeFile(config, 'server { listen 80; location / { return 503; } }');
  const compose = { services: { edge: { image: 'nginx:1.28-alpine', restart: 'unless-stopped', ports: ['127.0.0.1:8000:80'],
    volumes: [`${edge}:/etc/nginx/conf.d:ro`], networks: ['entry'] } }, networks: { entry: { external: true, name: 'store-next-entry' } } };
  await writeJson(join(edge, 'compose.json'), compose);
  run('docker', ['compose', '-p', 'store-next-entry', '-f', join(edge, 'compose.json'), 'up', '-d']);
  return edge;
}
async function health(release, port = 8000) {
  const response = await fetch(`http://localhost:${port}/api/health/ready`, { signal: AbortSignal.timeout(10000) });
  if (!response.ok || response.headers.get('x-store-release') !== release) throw new Error('Gateway readiness or release header mismatch');
  for (const path of ['/', '/admin/', '/my/work', '/admin/zone-tasks']) {
    const html = await fetch(`http://localhost:${port}${path}`, { signal: AbortSignal.timeout(10000) });
    if (!html.ok || !html.headers.get('content-type')?.includes('text/html')) throw new Error('SPA route verification failed');
  }
  const denied = await fetch(`http://localhost:${port}/api/dashboard`);
  if (denied.status !== 401 || !denied.headers.get('content-type')?.includes('application/json')) throw new Error('API unauthorized boundary failed');
  const missing = await fetch(`http://localhost:${port}/missing.js`);
  if (missing.status !== 404) throw new Error('Missing assets must not return HTML');
}
async function switchEdge(edge, release, configuration) {
  const config = join(edge, 'default.conf');
  const args = ['compose', '-p', 'store-next-entry', '-f', join(edge, 'compose.json')];
  await replaceGateway({
    read:()=>readFile(config),
    write:async value=>{await writeFile(config+'.next',value);await rename(config+'.next',config);},
    configuration:configuration??edgeConfig(release),
    validate:()=>run('docker',[...args,'exec','-T','edge','nginx','-t']),
    reload:()=>run('docker',[...args,'exec','-T','edge','nginx','-s','reload']),
    health:()=>release?health(release):Promise.resolve(),
  });
}
async function reclaimRetiredNetworks(target){
 const state=await exists(join(base,'local-state.json'))?await json(join(base,'local-state.json')):{};
 const known=[];for(const id of await readdir(base)){if(/^[a-z0-9-]+$/.test(id)&&await exists(join(base,id,'manifest.json')))known.push(id);}
 const ids=run('docker',['network','ls','--filter','label=com.docker.compose.project','--format','{{.ID}}'],{quiet:true}).split(/\s+/).filter(Boolean);if(!ids.length)return;
 const networks=JSON.parse(run('docker',['network','inspect',...ids],{quiet:true}));
 for(const network of retiredReleaseNetworks(networks,known,[state.current,state.previous,target])){
  const removed=spawnSync('docker',['network','rm',network.id],{encoding:'utf8'});
  if(removed.status===0)console.log('[release] reclaimed unused managed network '+network.name);
 }
}
async function deploy() {
  const dir = join(base, opts.release); const manifest = await json(join(dir, 'manifest.json'));
  if (manifest.id !== opts.release || manifest.env !== opts.env) throw new Error('Manifest target mismatch');
  assertDeployableManifest(manifest);
  progress('verify release checksums and assets');
  for (const name of ['index.html', 'admin/index.html']) if (digest(await readFile(join(dir, 'gateway-context/html', name))) !== manifest.html[name]) throw new Error('HTML checksum mismatch');
  await verifyAssets(manifest, dir, opts.command === 'deploy');
  for (const name of ['api', 'gateway']) if (await imageId(manifest.images[name]) !== manifest.imageDigests[name]) throw new Error('Runtime image checksum mismatch');
  const machine = run('docker', ['info', '--format', '{{.Architecture}}'], { quiet: true });
  const currentPlatform = ['aarch64', 'arm64'].includes(machine) ? 'linux/arm64' : 'linux/amd64';
  if (manifest.platform !== currentPlatform) throw new Error('Image platform differs from the local Docker target');
  const publicAssets=join(base,'public');await mkdir(publicAssets,{recursive:true});
  // Preserve all previously built local chunks, including when rolling back to an older gateway image.
  for(const name of await readdir(base)){
    const path=join(base,name,'manifest.json');
    if(!await exists(path))continue;
    const prior=await json(path);
    if(prior.assetsMode==='local')for(const app of ['admin','staff']){
      const source=join(base,name,'assets',app);
      if(await exists(source)){await mkdir(join(publicAssets,name),{recursive:true});await cp(source,join(publicAssets,name,app),{recursive:true});}
    }
  }
  const edge = await ensureEdge();
  const project = 'store-next-' + manifest.id;
  const args = ['compose', '-p', project, '-f', join(dir, 'compose.json')];
  let jobsVolume='store-next-jobs';
  if (opts.command === 'deploy') {
    await reclaimRetiredNetworks(manifest.id);
    progress('start candidate using explicit local Docker database');
    const deployment=localDeploymentDatabase(process.env);const url=deployment.url;jobsVolume=deployment.jobsVolume;
    if ((process.env.NEXT_SESSION_SECRET ?? '').length < 32) throw new Error('NEXT_SESSION_SECRET is missing');
    const legacyHost=process.env.NEXT_LEGACY_UPLOAD_HOST_DIRECTORY;
    if(legacyHost&&(!isAbsolute(legacyHost)||resolve(legacyHost)==='/'||!(await stat(legacyHost)).isDirectory()))throw new Error('Legacy upload host directory must be an existing absolute directory');
    const uploadVolumes=['jobs:/data/jobs',...(legacyHost?[{type:'bind',source:resolve(legacyHost),target:'/data/legacy-uploads',read_only:true,bind:{create_host_path:false}}]:[])];
    const extraKeys=['GOOGLE_PUBLIC_API_KEY','AI_API_KEY','AI_BASE_URL','AI_MODEL','VISIT_REPORT_AI','VISIT_LAYOUT_AI','RECON_LAYOUT_AI','LOGIN_NAME_AI','AI_AUTO_INTERPRET','AI_RECON_INTERPRET','VISIT_REPORT_LANGS','NEXT_REPORT_AI_TIMEOUT_MS','NEXT_REPORT_AI_MAX_TOKENS','NEXT_DEFAULT_STAFF_PASSWORD','BONUS_GROUP','BONUS_AMOUNT','BONUS_GROUP_SCHEDULE','BONUS_AMOUNT_SCHEDULE','STAFF_VISIBLE_FROM','DEDUP_SUB_STORES','STORE_AUTO_MERGE_EXACT','STORE_AI_BATCH','DEFAULT_VISIBLE_MAP','DEFAULT_DEPLOY_MAP','DEFAULT_POINT_RULES'];
    const extra=extraKeys.filter(key=>process.env[key]).map(key=>{const value=process.env[key];if(/[\r\n]/.test(value))throw new Error('Invalid multiline runtime configuration');return key+'='+value;}).join('\n');
    await writeFile(join(dir, 'runtime.env'), `NEXT_DATABASE_URL=${url.href}\nNEXT_SESSION_SECRET=${process.env.NEXT_SESSION_SECRET}\nNEXT_COOKIE_SECURE=false\nNEXT_HOST=0.0.0.0\n${Object.entries(localMcpEnvironment()).map(([key,value])=>key+'='+value).join('\n')}\nNEXT_TASK_DIRECTORY=/data/jobs\nNEXT_UPLOAD_DIRECTORY=/data/jobs/uploads\nNODE_ENV=production\nNEXT_LOCAL_HTTP=true\n${deployment.baseline?'NEXT_LOCAL_BASELINE=true\nNEXT_WORKER_RECONCILE=false\n':''}${legacyHost?'NEXT_LEGACY_UPLOAD_DIRECTORY=/data/legacy-uploads\n':''}${extra}\n`, { mode: 0o600 });
    const compose = { services: {
      api: { image: manifest.images.api, env_file: ['./runtime.env'], restart: 'unless-stopped', mem_limit: '512m', cpus: 1,volumes:uploadVolumes,depends_on:{jobsInit:{condition:'service_completed_successfully'}},
        networks: ['private'], healthcheck: { test: ['CMD', 'node', '-e', 'fetch("http://127.0.0.1:3000/api/health/ready").then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))'], interval: '5s', timeout: '3s', retries: 12 }, stop_grace_period: '30s' },
      jobsInit: {image:manifest.images.api,user:'0:0',command:['node','-e','require("fs").chownSync("/data/jobs",1000,1000)'],volumes:['jobs:/data/jobs'],networks:['private']},
      worker: { image: manifest.images.api,env_file:['./runtime.env'], profiles: ['worker'], depends_on:{jobsInit:{condition:'service_completed_successfully'}}, command: ['node','apps/worker/dist/apps/worker/src/main.js'],
        environment: {NEXT_TASK_DIRECTORY:'/data/jobs'}, volumes: uploadVolumes, restart:'unless-stopped', mem_limit:'512m',cpus:1,
        healthcheck:{test:['CMD','node','-e','const fs=require("fs");process.exit(Date.now()-Number(fs.readFileSync("/data/jobs/heartbeat","utf8"))<10000?0:1)'],interval:'5s',timeout:'3s',retries:6},stop_grace_period:'30s',networks:['private'] },
      gateway: { image: manifest.images.gateway, restart: 'unless-stopped', depends_on: { api: { condition: 'service_healthy' } }, mem_limit: '64m', networks: { private: {}, entry: { aliases: [`next-${manifest.id}`] } } },
    }, volumes: {jobs:{external:true,name:jobsVolume}}, networks: { private: {}, entry: { external: true, name: 'store-next-entry' } } };
    await writeJson(join(dir, 'compose.json'), compose);
    if (spawnSync('docker',['volume','inspect',jobsVolume],{stdio:'ignore'}).status!==0) run('docker',['volume','create',jobsVolume]);
    compose.services.gateway.volumes=[`${publicAssets}:/usr/share/nginx/html/r:ro`];
    await writeJson(join(dir,'compose.json'),compose);
    run('docker', [...args, 'config', '--quiet']);
    run('docker', [...args, 'up', '-d', '--wait', '--wait-timeout', '90']);
  } else {
    if (!await exists(join(dir, 'compose.json'))) throw new Error('Rollback target was never deployed');
    const saved=await json(join(dir,'compose.json'));jobsVolume=saved.volumes.jobs.name;saved.services.gateway.volumes=[`${publicAssets}:/usr/share/nginx/html/r:ro`];await writeJson(join(dir,'compose.json'),saved);
    run('docker', [...args, 'up', '-d', '--wait', '--wait-timeout', '90']);
  }
  progress('verify queued task compatibility before switching');
  run('docker',['run','--rm','--read-only','--user','1000:1000','-v',jobsVolume+':/data/jobs:ro',manifest.images.api,'node','-e',taskCompatibilityProbe(manifest.supportedTaskKinds??['system.check'])]);
  progress('candidate readiness before traffic switch');
  run('docker', [...args, 'exec', '-T', 'gateway', 'nginx', '-t']);
  run('docker', [...args, 'exec', '-T', 'gateway', 'wget', '-q', '-O', '/dev/null', 'http://127.0.0.1/api/health/ready']);
  progress('switch local gateway and smoke verify');
  const statePath = join(base, 'local-state.json'); const previous = await exists(statePath) ? await json(statePath) : null;
  let oldWorkerArgs;
  if(previous?.current && previous.current!==manifest.id){
    const path=join(base,previous.current,'compose.json');
    if(await exists(path) && (await json(path)).services.worker)oldWorkerArgs=['compose','-p','store-next-'+previous.current,'-f',path];
  }
  const previousGateway=await readFile(join(edge,'default.conf'));
  const hasWorker=Boolean((await json(join(dir,'compose.json'))).services.worker);
  await handoffRelease({
    stopOldWorker:()=>{if(oldWorkerArgs)run('docker',[...oldWorkerArgs,'stop','worker']);},
    verifyTasks:()=>run('docker',['run','--rm','--read-only','--user','1000:1000','-v',jobsVolume+':/data/jobs:ro',manifest.images.api,'node','-e',taskCompatibilityProbe(manifest.supportedTaskKinds??['system.check'])]),
    switchGateway:()=>switchEdge(edge,manifest.id),
    startWorker:()=>{if(hasWorker)run('docker',[...args,'--profile','worker','up','-d','--wait','--wait-timeout','60','worker']);},
    stopWorker:()=>{if(hasWorker)run('docker',[...args,'stop','worker']);},
    restoreGateway:()=>switchEdge(edge,previous?.current,previousGateway),
    startOldWorker:()=>{if(oldWorkerArgs)run('docker',[...oldWorkerArgs,'--profile','worker','up','-d','--wait','worker']);},
  });
  await writeJson(statePath, { current: manifest.id, previous: previous?.current ?? null, switchedAt: new Date().toISOString() });
  if (previous?.current && previous.current !== manifest.id) {
    const oldCompose = join(base, previous.current, 'compose.json');
    if (await exists(oldCompose)) run('docker', ['compose', '-p', 'store-next-' + previous.current, '-f', oldCompose, 'stop']);
  }
  console.log(JSON.stringify({ release: manifest.id, entry: 'http://localhost:8000', admin: 'http://localhost:8000/admin/', previous: previous?.current ?? null }, null, 2));
}
await mkdir(base, { recursive: true });
const lock = join(base, 'deploy-local.lock'); let locked = false;
try {
  if (await exists(opts.config)) process.loadEnvFile(resolve(opts.config));
  if (opts.command !== 'build') {
    try { await mkdir(lock); locked = true; await writeJson(join(lock, 'owner.json'), { pid: process.pid }); }
    catch { throw new Error('Another local deployment lock exists; verify its owner before removing it'); }
  }
  if (opts.command === 'build') await build(); else await deploy();
} catch (error) {
  // SDK/connection errors may contain credential URLs; never forward raw exceptions.
  console.error(error instanceof AggregateError ? `[release] FAILED at ${stage}; automatic recovery is incomplete. Check gateway and worker state before retrying.` : `[release] FAILED at ${stage}; check the current gateway and worker state and the preceding command output.`);
  process.exitCode = 1;
} finally { if (locked) await rm(lock, { recursive: true, force: true }); }
