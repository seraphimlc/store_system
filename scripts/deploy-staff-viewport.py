"""Staff Gateway-only encrypted overlay release. Never restart API/Worker or migrate a database."""
import sys,os,json,hashlib,subprocess,urllib.request,zipfile,tarfile,shutil,fcntl,time,importlib.util
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
spec=importlib.util.spec_from_file_location('common',str(Path(__file__).with_name('deploy-incremental.py')));common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common)
run,out,inspect,identity,health,sha=common.run,common.out,common.inspect,common.identity,common.health,common.sha

def main():
 action,r=sys.argv[1:];assert action in ('artifacts','candidate','switch','finalize','rollback','clean');assert __import__('re').fullmatch(r'prod-\d{8}-\d{6}',r)
 d=Path('/opt/store-next/releases')/r;b=Path('/opt/store-next/backups')/r;state=Path('/opt/store-next/production-state.json');compose=Path('/opt/store-next/runtime/compose.json');nginx=Path('/etc/nginx/conf.d/store.conf')
 lock=open('/opt/store-next/runtime/ui-release.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);start=time.time();m=json.load(open(d/'manifest.json')) if (d/'manifest.json').exists() else None
 def save(p,x):common.write_json(p,x)
 def stable(m):
  s=json.load(open(state));assert s['current']==m['sourceRelease']
  for n in ('api','worker'):
   x=inspect('store-next-prod-20261112-'+n+'-1');assert x['Id']==m['baselineContainers'][n]['id'] and x['Config']['Image']==m['baselineContainers'][n]['image'];health(x['Id'],10)
  assert identity(inspect(m['baseImages']['gateway']['tag']))==m['baseImages']['gateway']['identity'];return s
 def dc(*args):run('docker','compose','-p',m['baselineState']['composeProject'] if 'baselineState' in m else json.load(open(b/'state.before.json'))['composeProject'],'-f',str(compose),*args)
 def clean():
  if subprocess.run(['docker','inspect','staff-viewport-candidate'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:run('docker','rm','-f','staff-viewport-candidate',stdout=subprocess.DEVNULL)
  for n in ('decrypt-key.json','browser-private.json'):(d/n).unlink(missing_ok=True)
 def rollback():
  shutil.copy2(b/'compose.before.json',compose);dc('up','-d','--no-deps','gateway');health('store-next-prod-20261112-gateway-1',60);stable(m)
 if action=='artifacts':
  receipt=json.load(open(d/'encrypted-upload.json'));p=d/receipt['file'];urllib.request.urlretrieve(receipt['url'],p);assert p.stat().st_size==receipt['size'] and sha(p)==receipt['sha256']
  with zipfile.ZipFile(p) as z:
   assert sorted(z.namelist())==sorted(['image-overlay.tar.gz.enc','manifest.json','SHA256SUMS']) and z.testzip() is None;z.extractall(d)
  m=json.load(open(d/'manifest.json'));assert m['deploymentKind']=='staff-gateway-only-v1' and m['id']==r and m['releaseTag']==r and not m['dirty'] and m['migrations']==[] and set(m['images'])=={'gateway'}
  s=stable(m);run('sha256sum','--check','SHA256SUMS',cwd=d,stdout=subprocess.DEVNULL)
  run('docker','run','--rm','--network','none','--user','0','-v',str(d)+':/release','-w','/release',s['apiRelease'] and m['baselineContainers']['api']['image'],'node','--input-type=module','-e',"import{readFile}from'node:fs/promises';import{decryptArchive}from'./encrypted-release-bundle.mjs';await decryptArchive('image-overlay.tar.gz.enc','image-overlay.tar.gz','decrypt-key.json',JSON.parse(await readFile('manifest.json','utf8')).encryption)")
  allowed={'gateway.Dockerfile','gateway/default.conf','gateway/html/index.html'};assert {f['path'] for f in m['files']}==allowed;root=d/'overlay';root.mkdir(mode=0o700)
  with tarfile.open(d/'image-overlay.tar.gz') as t:
   common.validate_members(t.getmembers(),allowed)
   for e in t.getmembers():
    f=next(x for x in m['files'] if x['path']==e.name);raw=t.extractfile(e).read();assert len(raw)==f['size'] and hashlib.sha256(raw).hexdigest()==f['sha256'];p=root/e.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
  expected=f"FROM {m['baseImages']['gateway']['tag']}\nCOPY gateway/default.conf /etc/nginx/conf.d/default.conf\nCOPY gateway/html/ /usr/share/nginx/html/\n";assert (root/'gateway.Dockerfile').read_text()==expected
  with open(d/'image-build.log','w') as log:run('docker','build','--pull=false','--network','none','-f',str(root/'gateway.Dockerfile'),'-t',m['images']['gateway'],str(root),stdout=log,stderr=subprocess.STDOUT)
  image=inspect(m['images']['gateway']);base=m['baseImages']['gateway']['identity'];assert image['Architecture']=='amd64' and image['Config']==base['Config'] and image['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers']
  for path,digest in [('/etc/nginx/conf.d/default.conf',next(f['sha256'] for f in m['files'] if f['path']=='gateway/default.conf')),('/usr/share/nginx/html/index.html',m['html']['index.html']),('/usr/share/nginx/html/admin/index.html',m['html']['admin/index.html'])]:assert out('docker','run','--rm','--network','none','--entrypoint','sha256sum',m['images']['gateway'],path).split()[0]==digest
  (d/'decrypt-key.json').unlink();save(d/'server-image-verification.json',{'ok':True,'gatewayOnly':True,'basePinned':True,'adminHtmlUnchanged':True})
 elif action=='candidate':
  s=stable(m);b.mkdir(mode=0o700)
  for src,name in [(state,'state.before.json'),(compose,'compose.before.json'),(nginx,'nginx.before.conf'),(Path('/opt/store-next/runtime/runtime.env'),'runtime.before.env')]:shutil.copy2(src,b/name);os.chmod(b/name,0o600)
  network=m['baselineContainers']['gateway']['network'];run('docker','run','--rm','--network',network,'--entrypoint','nginx',m['images']['gateway'],'-t')
  run('docker','run','-d','--name','staff-viewport-candidate','--network',network,'-p','127.0.0.1:18080:80',m['images']['gateway'],stdout=subprocess.DEVNULL);health('staff-viewport-candidate',60)
 elif action=='switch':
  stable(m);assert json.load(open(d/'candidate-browser.json'))['ok'] and json.load(open(d/'candidate-bulk-browser.json'))['ok'];assert compose.read_bytes()==(b/'compose.before.json').read_bytes();assert nginx.read_bytes()==(b/'nginx.before.conf').read_bytes();clean();c=json.load(open(compose));c['services']['gateway']['image']=m['images']['gateway']
  try:
   save(compose,c);dc('up','-d','--no-deps','gateway');health('store-next-prod-20261112-gateway-1',60);common.wait_public_ready('https://store.visitworld.me/api/health/ready');stable(m)
   for path in ('/','/admin/'):
    h=out('curl','-fsSI','https://store.visitworld.me'+path);assert ('x-store-release: '+r) in h.lower()
   save(d/'switch.json',{'switchedAt':datetime.now(ZoneInfo('Asia/Singapore')).isoformat(),'release':r})
  except Exception:rollback();raise
 elif action=='finalize':
  stable(m);proof=json.load(open(d/'production-browser.json'));assert proof['ok'] and proof['release']==r;assert nginx.read_bytes()==(b/'nginx.before.conf').read_bytes();assert Path('/opt/store-next/runtime/runtime.env').read_bytes()==(b/'runtime.before.env').read_bytes();before=json.load(open(b/'compose.before.json'));c=json.load(open(compose));assert all(c['services'][n]==before['services'][n] for n in ('api','worker'));assert c['services']['gateway']['image']==m['images']['gateway'];health('store-next-prod-20261112-gateway-1',30)
  now=datetime.now(ZoneInfo('Asia/Singapore')).isoformat();s=json.load(open(state));s.update(current=r,previous=m['sourceRelease'],releaseTag=r,sourceCommit=m['sourceCommit'],staffRelease=r,backups=str(b),switchedAt=json.load(open(d/'switch.json'))['switchedAt'],verifiedAt=now,updatedAt=now,browserVerification='staff sheet visual viewport bounds, scroll, keyboard-height simulations and readonly/editable flows; API mocked, no provider or business writes')
  tmp=state.with_suffix('.pending.json');save(tmp,s);tmp.replace(state);save(d/'deployment-proof.json',{'ok':True,'release':r,'sourceCommit':m['sourceCommit'],'verifiedAt':now,'rollback':m['sourceRelease'],'apiContainerUnchanged':True,'workerContainerUnchanged':True,'adminHtmlUnchanged':True,'migrations':[]});clean()
 elif action=='rollback':rollback();clean()
 else:clean()
 p=d/'timings.json';timings=json.load(open(p)) if p.exists() else [];timings.append({'phase':action,'seconds':round(time.time()-start,3),'startedAt':datetime.fromtimestamp(start,ZoneInfo('Asia/Singapore')).isoformat()});save(p,timings);print(json.dumps({'ok':True,'phase':action,'release':r}),flush=True)
if __name__=='__main__':main()
