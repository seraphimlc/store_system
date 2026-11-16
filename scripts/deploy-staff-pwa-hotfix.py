"""Fixed identity overlay hotfix: no schema migration, no synthetic production data."""
import sys,json,subprocess,hashlib,zipfile,tarfile,urllib.request,os,shutil,fcntl,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
R=sys.argv[2];D=Path('/opt/store-next/releases')/R;B=Path('/opt/store-next/backups')/R
sys.path.insert(0,'/opt/store-next/releases/prod-20261116-161401')
from importlib.util import spec_from_file_location,module_from_spec
spec=spec_from_file_location('base','/opt/store-next/releases/prod-20261116-161401/deploy-attendance.py');base=module_from_spec(spec);spec.loader.exec_module(base)
def run(*args,**kw):return subprocess.run(args,check=True,**kw)
def out(*args):return subprocess.check_output(args,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n');os.chmod(p,0o600)
state=Path('/opt/store-next/production-state.json');compose=Path('/opt/store-next/runtime/compose.json');m=None
if (D/'manifest.json').exists():m=json.load(open(D/'manifest.json'))
def baseline():
 s=json.load(open(state));assert s['current']==m['sourceRelease']
 for k,x in m['baseImages'].items():assert base.identity(base.inspect(x['tag']))==x['identity']
 return s
with open('/opt/store-next/runtime/ui-release.lock','w') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if sys.argv[1]=='stage':
  receipt=json.load(open(D/'encrypted-upload.json'));z=D/receipt['file'];urllib.request.urlretrieve(receipt['url'],z);assert sha(z)==receipt['sha256']
  with zipfile.ZipFile(z) as archive:
   assert set(archive.namelist())=={'image-overlay.tar.gz.enc','manifest.json','SHA256SUMS'};archive.extractall(D)
  m=json.load(open(D/'manifest.json'));assert m['id']==R and m['migrations']==[];baseline()
  run('docker','run','--rm','--network','none','--user','0','-v',str(D)+':/release','-w','/release',m['baseImages']['api']['tag'],'node','--input-type=module','-e',"import{readFile}from'node:fs/promises';import{decryptArchive}from'./encrypted-release-bundle.mjs';await decryptArchive('image-overlay.tar.gz.enc','image-overlay.tar.gz','decrypt-key.json',JSON.parse(await readFile('manifest.json','utf8')).encryption)")
  allowed={'api.Dockerfile','gateway.Dockerfile','api/apps/server/src/attendance.js','gateway/default.conf','gateway/html/index.html',*[ 'gateway/html/'+x for x in ['sw.js','manifest.webmanifest','pwa/offline.html','pwa/icon-192.png','pwa/icon-512.png','pwa/apple-touch-icon.png','pwa/sw-disable.js']]};assert {f['path'] for f in m['files']}==allowed
  root=D/'overlay';root.mkdir(mode=0o700)
  with tarfile.open(D/'image-overlay.tar.gz') as archive:
   base.validate_members(archive.getmembers(),allowed)
   for f in m['files']:
    data=archive.extractfile(f['path']).read();assert len(data)==f['size'] and hashlib.sha256(data).hexdigest()==f['sha256'];p=root/f['path'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
  for k in ['api','gateway']:
   expected=f"FROM {m['baseImages'][k]['tag']}\n"+('COPY api/ /app/apps/server/dist/\n' if k=='api' else 'COPY gateway/default.conf /etc/nginx/conf.d/default.conf\nCOPY gateway/html/ /usr/share/nginx/html/\n');assert (root/(k+'.Dockerfile')).read_text()==expected
   with open(D/(k+'-image-build.log'),'w') as log:run('docker','build','--network','none','--pull=false','-f',str(root/(k+'.Dockerfile')),'-t',m['images'][k],str(root),stdout=log,stderr=subprocess.STDOUT)
   image=base.inspect(m['images'][k]);old=m['baseImages'][k]['identity'];assert image['Architecture']=='amd64' and image['Config']==old['Config'] and image['RootFS']['Layers'][:len(old['RootFS']['Layers'])]==old['RootFS']['Layers']
  (D/'decrypt-key.json').unlink();run('docker','run','--rm','--network','none','--entrypoint','node',m['images']['api'],'-e',"const fs=require('fs');if(fs.readFileSync('/app/apps/server/dist/apps/server/src/attendance.js','utf8').includes('定位已过期'))process.exit(1);require('/app/apps/server/dist/apps/server/src/attendance.js');console.log('attendance module load passed')")
  save(D/'server-image-verification.json',{'ok':True,'configsInherited':True,'expiryRemoved':True,'pwaFiles':len(m['staffPwa'])})
 elif sys.argv[1]=='switch':
  old=baseline();assert json.load(open(D/'browser-verification.json'))['ok'];B.mkdir(mode=0o700)
  for src,name in [(state,'state.before.json'),(compose,'compose.before.json'),(Path('/opt/store-next/runtime/runtime.env'),'runtime.before.env')]:shutil.copy2(src,B/name);os.chmod(B/name,0o600)
  with open(B/'database.before.dump','wb') as f:run('docker','exec','deploy-db-1','sh','-c','pg_dump -U "$POSTGRES_USER" -d store_settle -Fc --no-owner --no-acl',stdout=f)
  os.chmod(B/'database.before.dump',0o600)
  with open(B/'database.before.dump','rb') as f:run('docker','exec','-i','deploy-db-1','pg_restore','--list',stdin=f,stdout=subprocess.DEVNULL)
  dc=['docker','compose','-p',old['composeProject'],'-f',str(compose)];c=json.load(open(compose));before=json.loads(json.dumps(c));c['services']['api']['image']=m['images']['api'];c['services']['gateway']['image']=m['images']['gateway'];assert c['services']['worker']==before['services']['worker']
  try:
   save(compose,c);run(*(dc+['up','-d','--no-deps','api']));base.health('store-next-prod-20261112-api-1');run(*(dc+['up','-d','--no-deps','gateway']));base.health('store-next-prod-20261112-gateway-1',60);base.wait_public_ready('https://store.visitworld.me/api/health/ready')
   admin=out('docker','exec','store-next-prod-20261112-gateway-1','sha256sum','/usr/share/nginx/html/admin/index.html').split()[0];assert admin==m['html']['admin/index.html']
   for f in m['staffPwa']:
    value=out('docker','exec','store-next-prod-20261112-gateway-1','sha256sum','/usr/share/nginx/html/'+f['path']).split()[0];assert value==f['sha256']
   now=datetime.now(ZoneInfo('Asia/Singapore')).isoformat();save(state,{**old,'current':R,'previous':old['current'],'releaseTag':R,'sourceCommit':m['sourceCommit'],'apiRelease':R,'staffRelease':R,'backups':str(B),'switchedAt':now,'updatedAt':now,'verifiedAt':now,'pwaEnabled':True,'browserVerification':'exact frozen staff build PWA lifecycle; module load; public headers/checksums'});save(D/'deployment-proof.json',{'ok':True,'release':R,'sourceCommit':m['sourceCommit'],'rollback':old['current'],'pwa':True,'locationExpiryRemoved':True,'adminPreserved':True,'workerPreserved':True,'migrations':[]})
  except Exception:
   shutil.copy2(B/'compose.before.json',compose);run(*(dc+['up','-d','--no-deps','api','gateway']));base.health('store-next-prod-20261112-api-1');base.health('store-next-prod-20261112-gateway-1');raise
 print(json.dumps({'ok':True,'phase':sys.argv[1],'release':R}))
