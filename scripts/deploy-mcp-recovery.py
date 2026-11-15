"""MCP recovery through the existing encrypted OSS / stable Compose deployment phases."""
import importlib.util,json,os,re,shutil,tarfile,hashlib,time,zipfile,urllib.request,subprocess,argparse,fcntl
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
spec=importlib.util.spec_from_file_location('incremental',Path(__file__).with_name('deploy-incremental.py'));core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
run,out,inspect,health,write_json=core.run,core.out,core.inspect,core.health,core.write_json
class Recovery(core.Deployment):
 def artifacts(self):
  receipt=json.load(open(self.d/'encrypted-upload.json'));p=self.d/receipt['file'];urllib.request.urlretrieve(receipt['url'],p);assert p.stat().st_size==receipt['size'] and core.sha(p)==receipt['sha256']
  with zipfile.ZipFile(p) as z:
   assert sorted(z.namelist())==sorted(['image-overlay.tar.gz.enc','manifest.json','SHA256SUMS']) and z.testzip() is None;z.extractall(self.d)
  self.m=json.load(open(self.d/'manifest.json'));m=self.m;assert m['id']==self.r and m['releaseTag']==self.r and not m['dirty'] and m['artifactKind']=='image-overlay-v1' and m['migrations']==['001-auth-security.sql'];self.baseline()
  run('sha256sum','--check','SHA256SUMS',cwd=self.d,stdout=subprocess.DEVNULL)
  run('docker','run','--rm','--network','none','--user','0','-v',str(self.d)+':/release','-w','/release',m['baseImages']['api']['tag'],'node','--input-type=module','-e',"import{readFile}from'node:fs/promises';import{decryptArchive}from'./encrypted-release-bundle.mjs';await decryptArchive('image-overlay.tar.gz.enc','image-overlay.tar.gz','decrypt-key.json',JSON.parse(await readFile('manifest.json','utf8')).encryption)")
  root=self.d/'overlay';root.mkdir(mode=0o700);expected={f['path']:f for f in m['files']};assert len(expected)==len(m['files'])
  for name in expected:assert name in ['api.Dockerfile','gateway.Dockerfile','gateway/default.conf','gateway/html/admin/index.html'] or re.fullmatch(r'api/(apps/server/src|packages/(domain|contracts)/src)/[a-zA-Z0-9_-]+\.js',name) or name.startswith('vendor/node_modules/') or name in ['vendor/package.json','vendor/package-lock.json']
  with tarfile.open(self.d/'image-overlay.tar.gz') as t:
   core.validate_members(t.getmembers(),expected)
   for member in t.getmembers():
    meta=expected[member.name];raw=t.extractfile(member).read();assert member.size==meta['size'] and hashlib.sha256(raw).hexdigest()==meta['sha256'];p=root/member.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
  dockerfiles={'api':f"FROM {m['baseImages']['api']['tag']}\nCOPY api/ /app/apps/server/dist/\nCOPY vendor/ /app/mcp-dependencies/\nENV NODE_PATH=/app/mcp-dependencies/node_modules\n",'gateway':f"FROM {m['baseImages']['gateway']['tag']}\nCOPY gateway/default.conf /etc/nginx/conf.d/default.conf\nCOPY gateway/html/ /usr/share/nginx/html/\n"}
  for kind in ['api','gateway']:
   assert (root/(kind+'.Dockerfile')).read_text()==dockerfiles[kind]
   with open(self.d/(kind+'-image-build.log'),'w') as log:run('docker','build','--pull=false','--network','none','-f',str(root/(kind+'.Dockerfile')),'-t',m['images'][kind],str(root),stdout=log,stderr=subprocess.STDOUT)
   image=inspect(m['images'][kind]);base=m['baseImages'][kind]['identity'];assert image['Architecture']=='amd64' and image['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers'];config=dict(base['Config'])
   if kind=='api':config['Env']=[x for x in config['Env'] if not x.startswith('NODE_PATH=')]+['NODE_PATH=/app/mcp-dependencies/node_modules']
   assert image['Config']==config
   entries=[]
   for name,f in expected.items():
    dest='/app/apps/server/dist/'+name[4:] if name.startswith('api/') else '/app/mcp-dependencies/'+name[7:] if name.startswith('vendor/') else '/etc/nginx/conf.d/default.conf' if name=='gateway/default.conf' else '/usr/share/nginx/'+name[8:] if name.startswith('gateway/html/') else None
    if dest and ((kind=='api' and name.startswith(('api/','vendor/'))) or (kind=='gateway' and name.startswith('gateway/'))):entries.append((dest,f['sha256']))
   if kind=='api':
    script="const fs=require('fs'),crypto=require('crypto');for(const [p,h]of "+json.dumps(entries)+"){if(crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')!==h)process.exit(1)}"
    run('docker','run','--rm','-i','--network','none','--entrypoint','node',m['images'][kind],input=script.encode())
   else:
    for dest,digest in entries:assert out('docker','run','--rm','--network','none','--entrypoint','sha256sum',m['images'][kind],dest).split()[0]==digest
  (self.d/'decrypt-key.json').unlink();write_json(self.d/'server-image-verification.json',{'ok':True,'basePinned':True,'filesVerified':len(expected),'runtimeConfigChange':'SDK NODE_PATH only'})
 def prepare(self):
  super().prepare();self.schema((self.d/'candidate-database.txt').read_text().strip());p=self.d/'candidate.env';p.write_text(p.read_text()+'\nNEXT_MCP_PUBLIC_URL=https://store.visitworld.me/mcp\nNEXT_MCP_AUTHORIZATION_UI_URL=https://store.visitworld.me/admin/mcp-authorize\n')
 def schema(self,name):
  assert name=='store_settle' or re.fullmatch(r'ui_candidate_\d{8}_\d{6}',name)
  with open(self.d/'001-auth-security.sql','rb') as f:run('docker','exec','-i','deploy-db-1','sh','-c','psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"','sh',name,stdin=f,stdout=subprocess.DEVNULL)
 def smoke(self,container,mode):
  run('docker','cp',str(self.d/'mcp-recovery-smoke.cjs'),container+':/tmp/mcp-recovery-smoke.cjs')
  with open(self.d/(mode+'-smoke.json'),'w') as f:run('docker','exec',container,'node','/tmp/mcp-recovery-smoke.cjs',mode,stdout=f)
 def switch(self):
  self.baseline();assert json.load(open(self.d/'candidate-smoke.json'))['ok'];assert self.compose.read_bytes()==(self.b/'compose.before.json').read_bytes();assert self.nginx.read_bytes()==(self.b/'nginx.before.conf').read_bytes();self.clean()
  original=self.nginx.read_text();marker='    location / {\n        proxy_pass http://127.0.0.1:8000;';assert original.count(marker)==1
  try:
   self.nginx.write_text(original.replace(marker,'    location / {\n        add_header Retry-After "120" always;\n        return 503;\n        proxy_pass http://127.0.0.1:8000;'));run('nginx','-t');run('nginx','-s','reload');time.sleep(3)
   jobs=Path('/var/lib/docker/volumes/store-next-prod-jobs/_data');assert not list((jobs/'running').glob('*'))
   assert out('docker','exec','deploy-db-1','sh','-c','psql -U "$POSTGRES_USER" -d store_settle -Atc "$1"','sh',"SELECT count(*) FROM pg_stat_activity WHERE datname='store_settle' AND pid<>pg_backend_pid() AND state='active' AND xact_start IS NOT NULL")=='0'
   self.dc('--profile','worker','stop','-t','60','worker','api')
   for name in ['api','worker']:assert not inspect('store-next-prod-20261112-'+name+'-1')['State']['Running']
   with open(self.b/'database.final.dump','wb') as f:run('docker','exec','deploy-db-1','sh','-c','pg_dump -U "$POSTGRES_USER" -d store_settle -Fc --no-owner --no-acl',stdout=f)
   os.chmod(self.b/'database.final.dump',0o600)
   with open(self.b/'database.final.dump','rb') as f:run('docker','exec','-i','deploy-db-1','pg_restore','--list',stdin=f,stdout=subprocess.DEVNULL)
   self.schema('store_settle');c=json.load(open(self.b/'compose.before.json'));c['services']['api']['image']=self.m['images']['api'];c['services']['gateway']['image']=self.m['images']['gateway'];c['services']['api'].setdefault('environment',{}).update({'NEXT_MCP_PUBLIC_URL':'https://store.visitworld.me/mcp','NEXT_MCP_AUTHORIZATION_UI_URL':'https://store.visitworld.me/admin/mcp-authorize'});write_json(self.compose,c)
   self.dc('up','-d','--no-deps','api');health('store-next-prod-20261112-api-1');self.smoke('store-next-prod-20261112-api-1','production');self.dc('--profile','worker','up','-d','--no-deps','worker');health('store-next-prod-20261112-worker-1',60);self.dc('up','-d','--no-deps','gateway');health('store-next-prod-20261112-gateway-1',60)
   block='''    location ~ ^/(mcp(/|$)|oauth/|[.]well-known/oauth-(authorization-server|protected-resource)$) {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
        gzip off;
        client_max_body_size 25m;
    }
'''
   for route in ['/mcp','/.well-known/','/oauth/']:
    pattern=r'    location '+re.escape(route)+r' \{ default_type application/json; return 404; \}\n';assert len(re.findall(pattern,original))==1;original=re.sub(pattern,'',original)
   original=original.replace(marker,block+'\n'+marker);self.nginx.write_text(original);run('nginx','-t');run('nginx','-s','reload');core.wait_public_ready('https://store.visitworld.me/api/health/ready');write_json(self.d/'switch.json',{'release':self.r,'switchedAt':datetime.now(ZoneInfo('Asia/Singapore')).isoformat()})
  except Exception:self.rollback();raise
 def finalize(self):
  assert json.load(open(self.d/'production-smoke.json'))['ok'];assert json.load(open(self.d/'public-mcp-proof.json'))['ok'];old=json.load(open(self.b/'state.before.json'));c=json.load(open(self.compose));before=json.load(open(self.b/'compose.before.json'));assert c['services']['worker']==before['services']['worker'];assert Path('/opt/store-next/runtime/runtime.env').read_bytes()==(self.b/'runtime.before.env').read_bytes()
  for n in ['api','gateway','worker']:health('store-next-prod-20261112-'+n+'-1',20)
  now=datetime.now(ZoneInfo('Asia/Singapore')).isoformat();s={**old,'current':self.r,'previous':old['current'],'releaseTag':self.r,'sourceCommit':self.m['sourceCommit'],'apiRelease':self.r,'adminRelease':self.r,'staffRelease':old['staffRelease'],'workerRelease':old['workerRelease'],'backups':str(self.b),'updatedAt':now,'verifiedAt':now,'mcpRecovery':True};tmp=self.state.with_suffix('.pending.json');write_json(tmp,s);tmp.replace(self.state);write_json(self.d/'deployment-proof.json',{'ok':True,'release':self.r,'rollback':old['current'],'sourceCommit':self.m['sourceCommit'],'verifiedAt':now,'staffUnchanged':True,'workerImageUnchanged':True,'migration':'001-auth-security.sql','publicMcp':True});self.clean()
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['artifacts','prepare','candidate','switch','finalize','rollback','clean']);p.add_argument('release');a=p.parse_args();d=Recovery(a.release)
 with open('/opt/store-next/runtime/ui-release.lock','w') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);d.phase(a.action,getattr(d,a.action));print(json.dumps({'ok':True,'phase':a.action,'release':a.release}),flush=True)
if __name__=='__main__':main()
