"""Immutable encrypted image-overlay deployment; fixed-baseline build, isolated candidate, rollback."""
import argparse,ast,fcntl,hashlib,json,os,re,shutil,subprocess,tarfile,time,urllib.request,zipfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

def ready_state(state,probe):
    if not state.get('Running') or state.get('OOMKilled'):return False
    h=state.get('Health')
    return h.get('Status')=='healthy' if h else probe()

def validate_members(members,expected):
    names=[x.name for x in members]
    if len(names)!=len(set(names)) or set(names)!=set(expected):raise ValueError('archive whitelist mismatch')
    for m in members:
        if not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts:raise ValueError('unsafe archive member')

def run(*a,**kw):return subprocess.run(a,check=True,**kw)
def out(*a):return subprocess.check_output(a,text=True).strip()
def sha(p):
    with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def inspect(tag):return json.loads(out('docker','inspect',tag))[0]
def identity(image):return {k:image[k] for k in ('Architecture','Os','Config','RootFS')}
def health(name,seconds=240):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        state=inspect(name)['State']
        def probe():return subprocess.run(['docker','exec',name,'node','-e',"fetch('http://127.0.0.1:3000/api/health/ready',{signal:AbortSignal.timeout(2000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
        if ready_state(state,probe):return
        if not state['Running']:raise RuntimeError('service stopped: '+name)
        time.sleep(2)
    raise RuntimeError('readiness timeout: '+name)
def wait_public_ready(url,attempts=15):
    # Nginx reload is asynchronous: retry only this safe GET, never a business write.
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url,timeout=5) as response:
                if response.status==200 and json.load(response).get('ok'):return
        except (OSError,ValueError):pass
        time.sleep(1)
    raise RuntimeError('public readiness did not recover after nginx reload')

def write_json(p,x):p.write_text(json.dumps(x,indent=2)+'\n');os.chmod(p,0o600)


def download_artifact(receipt,path,timeout=20):
    url=receipt['url']
    if not url.startswith('https://') or Path(receipt['file']).name!=receipt['file']:raise ValueError('Invalid encrypted artifact receipt')
    tmp=path.with_suffix(path.suffix+'.pending')
    try:
        with urllib.request.urlopen(url,timeout=timeout) as response,open(tmp,'wb') as target:
            size=0
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>receipt['size']:raise ValueError('Artifact larger than receipt')
                target.write(chunk)
        if size!=receipt['size'] or sha(tmp)!=receipt['sha256']:raise ValueError('Artifact receipt mismatch')
        tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)

class Deployment:
    def __init__(self,r):
        if not re.fullmatch(r'prod-\d{8}-\d{6}',r):raise ValueError('release id')
        self.r=r;self.d=Path('/opt/store-next/releases')/r;self.b=Path('/opt/store-next/backups')/r
        self.state=Path('/opt/store-next/production-state.json');self.compose=Path('/opt/store-next/runtime/compose.json');self.nginx=Path('/etc/nginx/conf.d/store.conf')
        self.m=json.load(open(self.d/'manifest.json')) if (self.d/'manifest.json').exists() else None
    def phase(self,name,action):
        start=time.monotonic();p=self.d/'timings.json';items=json.load(open(p)) if p.exists() else []
        entry={'phase':name,'startedAt':datetime.now(ZoneInfo('Asia/Singapore')).isoformat(),'status':'running'}
        items.append(entry);write_json(p,items)
        try:
            result=action();entry['status']='passed';return result
        except Exception as error:
            entry['status']='failed';entry['errorType']=type(error).__name__;raise
        finally:
            entry['endedAt']=datetime.now(ZoneInfo('Asia/Singapore')).isoformat();entry['seconds']=round(time.monotonic()-start,3)
            # Nested measured steps may already have appended entries.
            current=json.load(open(p));index=next(i for i,x in enumerate(current) if x['startedAt']==entry['startedAt'] and x['phase']==name);current[index]=entry;write_json(p,current)
    def preflight(self):
        required=['encrypted-upload.json','decrypt-key.json','encrypted-release-bundle.mjs','asset-storage.mjs','attendance-smoke.cjs']
        for name in required:
            p=self.d/name
            if not p.is_file():raise RuntimeError('Missing release helper: '+name)
        if (self.d/'decrypt-key.json').stat().st_mode & 0o077:raise RuntimeError('Decrypt key must be private')
        ast.parse((self.d/'deploy-attendance.py').read_text())
        state=json.load(open(self.state));base=json.load(open(self.compose))['services']['api']['image']
        if self.m is not None:self.baseline()
        # Exercise the real decrypt import graph and smoke syntax before downloading artifacts/restoring a DB.
        run('docker','run','--rm','--network','none','--user','0','-v',str(self.d)+':/release:ro','-w','/release',base,'node','--input-type=module','-e',"await import('./encrypted-release-bundle.mjs')")
        run('docker','run','--rm','--network','none','--user','0','-v',str(self.d)+':/release:ro','-w','/release',base,'node','--check','attendance-smoke.cjs')
        write_json(self.d/'helper-preflight.json',{'ok':True,'baseline':state['current'],'helperImports':True,'smokeSyntax':True})
    def baseline(self):
        s=json.load(open(self.state));assert s['current']==self.m['sourceRelease'],'production baseline changed'
        for kind,x in self.m['baseImages'].items():assert identity(inspect(x['tag']))==x['identity'],'base image changed: '+kind
        return s
    def artifacts(self):
        self.phase('helper-preflight',self.preflight)
        receipt=json.load(open(self.d/'encrypted-upload.json'));p=self.d/receipt['file'];download_artifact(receipt,p)
        assert p.stat().st_size==receipt['size'] and sha(p)==receipt['sha256']
        with zipfile.ZipFile(p) as z:
            assert sorted(z.namelist())==sorted(['image-overlay.tar.gz.enc','manifest.json','SHA256SUMS']) and z.testzip() is None
            z.extractall(self.d)
        self.m=json.load(open(self.d/'manifest.json'));m=self.m
        assert m['id']==self.r and m['releaseTag']==self.r and not m['dirty'] and m['artifactKind']=='image-overlay-v1' and m['migrations']==['010-attendance.sql']
        run('sha256sum','--check','SHA256SUMS',cwd=self.d,stdout=subprocess.DEVNULL);self.baseline()
        run('docker','run','--rm','--network','none','--user','0','-v',str(self.d)+':/release','-w','/release',m['baseImages']['api']['tag'],'node','--input-type=module','-e',"import{readFile}from'node:fs/promises';import{decryptArchive}from'./encrypted-release-bundle.mjs';await decryptArchive('image-overlay.tar.gz.enc','image-overlay.tar.gz','decrypt-key.json',JSON.parse(await readFile('manifest.json','utf8')).encryption)")
        root=self.d/'overlay';root.mkdir(mode=0o700)
        allowed={'api.Dockerfile','gateway.Dockerfile','api/apps/server/src/main.js','api/apps/server/src/attendance.js','gateway/default.conf','gateway/html/admin/index.html','gateway/html/index.html','migrations/010-attendance.sql'}
        assert {f['path'] for f in m['files']}==allowed
        with tarfile.open(self.d/'image-overlay.tar.gz') as t:
            validate_members(t.getmembers(),allowed)
            for x in t.getmembers():
                f=next(f for f in m['files'] if f['path']==x.name);assert x.size==f['size'];raw=t.extractfile(x).read();assert hashlib.sha256(raw).hexdigest()==f['sha256'];p=root/x.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        expected={'api':f"FROM {m['baseImages']['api']['tag']}\nCOPY api/ /app/apps/server/dist/\n",'gateway':f"FROM {m['baseImages']['gateway']['tag']}\nCOPY gateway/default.conf /etc/nginx/conf.d/default.conf\nCOPY gateway/html/ /usr/share/nginx/html/\n"}
        for k in ('api','gateway'):
            assert (root/(k+'.Dockerfile')).read_text()==expected[k]
            with open(self.d/(k+'-image-build.log'),'w') as log:self.phase(k+'-image-build',lambda:run('docker','build','--pull=false','--network','none','-f',str(root/(k+'.Dockerfile')),'-t',m['images'][k],str(root),stdout=log,stderr=subprocess.STDOUT))
            x=inspect(m['images'][k]);base=m['baseImages'][k]['identity'];assert x['Architecture']=='amd64' and x['Config']==base['Config'] and x['RootFS']['Layers'][:len(base['RootFS']['Layers'])]==base['RootFS']['Layers']
            for f in [f for f in m['files'] if f['path'].startswith(k+'/')]:
                if k=='api':dest='/app/apps/server/dist/'+f['path'][4:]
                elif f['path']=='gateway/default.conf':dest='/etc/nginx/conf.d/default.conf'
                else:dest='/usr/share/nginx/'+f['path'][8:]
                actual=out('docker','run','--rm','--network','none','--user','0','--entrypoint','sha256sum',m['images'][k],dest).split()[0];assert actual==f['sha256']
        (self.d/'decrypt-key.json').unlink();write_json(self.d/'server-image-verification.json',{'ok':True,'baseImagesPinned':True,'configsInherited':True,'filesVerified':len(allowed),'images':m['images']})
    def prepare(self):
        self.baseline();self.b.mkdir(mode=0o700)
        for src,name in [(self.state,'state.before.json'),(self.compose,'compose.before.json'),(self.nginx,'nginx.before.conf'),(Path('/opt/store-next/runtime/runtime.env'),'runtime.before.env')]:shutil.copy2(src,self.b/name);os.chmod(self.b/name,0o600)
        with open(self.b/'database.before.dump','wb') as f:self.phase('database-before-backup',lambda:run('docker','exec','deploy-db-1','sh','-c','pg_dump -U "$POSTGRES_USER" -d store_settle -Fc --no-owner --no-acl',stdout=f))
        os.chmod(self.b/'database.before.dump',0o600)
        with open(self.b/'database.before.dump','rb') as f,open(self.b/'database.before.list','w') as o:run('docker','exec','-i','deploy-db-1','pg_restore','--list',stdin=f,stdout=o)
        name='attendance_candidate_'+self.r[5:].replace('-','_');(self.d/'candidate-database.txt').write_text(name)
        run('docker','exec','deploy-db-1','sh','-c','createdb -U "$POSTGRES_USER" "$1"','sh',name)
        with open(self.b/'database.before.dump','rb') as f,open(self.d/'candidate-restore.log','w') as log:self.phase('candidate-database-restore',lambda:run('docker','exec','-i','deploy-db-1','sh','-c','pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-acl --exit-on-error','sh',name,stdin=f,stdout=log,stderr=subprocess.STDOUT))
        with open(self.d/'overlay/migrations/010-attendance.sql','rb') as f:self.phase('candidate-migration',lambda:run('docker','exec','-i','deploy-db-1','sh','-c','psql -Xq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"','sh',name,stdin=f))
        from urllib.parse import urlsplit,urlunsplit
        env=dict(l.split('=',1) for l in (self.b/'runtime.before.env').read_text().splitlines() if l and not l.startswith('#') and '=' in l);u=urlsplit(env['NEXT_DATABASE_URL']);env['NEXT_DATABASE_URL']=urlunsplit((u.scheme,u.netloc,'/'+name,u.query,u.fragment));env['NEXT_TASK_DIRECTORY']='/tmp/ui-jobs';env['NEXT_WORKER_RECONCILE']='false'
        (self.d/'candidate.env').write_text('\n'.join(k+'='+v for k,v in env.items())+'\n');os.chmod(self.d/'candidate.env',0o600)
    def candidate(self):
        network='attendance-candidate-'+self.r;run('docker','network','create',network,stdout=subprocess.DEVNULL)
        run('docker','run','-d','--name','attendance-candidate-api','--network','deploy_default','--memory','1024m','--cpus','1.5','--env-file',str(self.d/'candidate.env'),self.m['images']['api'],stdout=subprocess.DEVNULL)
        run('docker','network','connect','--alias','api',network,'attendance-candidate-api');health('attendance-candidate-api')
        run('docker','run','--rm','--network',network,'--entrypoint','nginx',self.m['images']['gateway'],'-t')
        run('docker','run','-d','--name','attendance-candidate-gateway','--network',network,'-p','127.0.0.1:18080:80',self.m['images']['gateway'],stdout=subprocess.DEVNULL);health('attendance-candidate-gateway',60)
        self.smoke('attendance-candidate-api','candidate')
    def smoke(self,container,mode):
        run('docker','cp',str(self.d/'attendance-smoke.cjs'),container+':/tmp/attendance-smoke.cjs')
        run('docker','exec','--user','0',container,'chown','node:node','/tmp/attendance-smoke.cjs')
        run('docker','exec','--user','0',container,'chmod','400','/tmp/attendance-smoke.cjs')
        with open(self.d/(mode+'-smoke.json'),'w') as f:run('docker','exec','-e','ATTENDANCE_MODE='+mode,container,'node','/tmp/attendance-smoke.cjs',stdout=f)
    def clean(self):
        for container in ('attendance-candidate-gateway','attendance-candidate-api'):
            if subprocess.run(['docker','inspect',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:run('docker','rm','-f',container,stdout=subprocess.DEVNULL)
        subprocess.run(['docker','network','rm','attendance-candidate-'+self.r],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        p=self.d/'candidate-database.txt'
        if p.exists():
            name=p.read_text().strip();assert re.fullmatch(r'attendance_candidate_\d{8}_\d{6}',name)
            run('docker','exec','deploy-db-1','sh','-c','dropdb --if-exists -U "$POSTGRES_USER" "$1"','sh',name)
        for n in ('candidate.env','decrypt-key.json','browser-private.json'):(self.d/n).unlink(missing_ok=True)
    def dc(self,*args):
        state=json.load(open(self.b/'state.before.json'));run('docker','compose','-p',state['composeProject'],'-f',str(self.compose),*args)
    def rollback(self):
        shutil.copy2(self.b/'compose.before.json',self.compose)
        for name in ('api','worker','gateway'):
            self.dc(*(['--profile','worker'] if name=='worker' else []),'up','-d','--no-deps',name);health('store-next-prod-20261112-'+name+'-1')
        shutil.copy2(self.b/'nginx.before.conf',self.nginx);run('nginx','-t');run('nginx','-s','reload');shutil.copy2(self.b/'state.before.json',self.state)
    def switch(self):
        self.baseline();assert json.load(open(self.d/'candidate-browser.json'))['ok'];assert json.load(open(self.d/'candidate-smoke.json'))['ok']
        assert self.compose.read_bytes()==(self.b/'compose.before.json').read_bytes();assert self.nginx.read_bytes()==(self.b/'nginx.before.conf').read_bytes();self.clean()
        original=self.nginx.read_text();marker='    location / {\n        proxy_pass http://127.0.0.1:8000;';assert original.count(marker)==1
        try:
            self.nginx.write_text(original.replace(marker,'    location / {\n        add_header Retry-After "120" always;\n        return 503;\n        proxy_pass http://127.0.0.1:8000;'));run('nginx','-t');run('nginx','-s','reload');time.sleep(3)
            jobs=Path('/var/lib/docker/volumes/store-next-prod-jobs/_data');assert not list((jobs/'running').glob('*'))
            sql="SELECT count(*) FROM pg_stat_activity WHERE datname='store_settle' AND pid<>pg_backend_pid() AND state='active' AND xact_start IS NOT NULL"
            assert out('docker','exec','deploy-db-1','sh','-c','psql -U "$POSTGRES_USER" -d store_settle -Atc "$1"','sh',sql)=='0'
            self.dc('--profile','worker','stop','-t','60','worker','api')
            for name in ('api','worker'):
                s=inspect('store-next-prod-20261112-'+name+'-1')['State'];assert not s['Running'] and s['ExitCode']!=137
            with open(self.b/'database.final.dump','wb') as f:self.phase('database-final-backup',lambda:run('docker','exec','deploy-db-1','sh','-c','pg_dump -U "$POSTGRES_USER" -d store_settle -Fc --no-owner --no-acl',stdout=f))
            os.chmod(self.b/'database.final.dump',0o600)
            with open(self.b/'database.final.dump','rb') as f:run('docker','exec','-i','deploy-db-1','pg_restore','--list',stdin=f,stdout=subprocess.DEVNULL)
            with open(self.d/'overlay/migrations/010-attendance.sql','rb') as f:self.phase('production-migration',lambda:run('docker','exec','-i','deploy-db-1','sh','-c','psql -Xq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d store_settle','sh',stdin=f))
            c=json.load(open(self.b/'compose.before.json'));c['services']['api']['image']=self.m['images']['api'];c['services']['gateway']['image']=self.m['images']['gateway'];write_json(self.compose,c)
            self.dc('up','-d','--no-deps','api');health('store-next-prod-20261112-api-1');self.smoke('store-next-prod-20261112-api-1','production')
            self.dc('--profile','worker','up','-d','--no-deps','worker');health('store-next-prod-20261112-worker-1',60)
            self.dc('up','-d','--no-deps','gateway');health('store-next-prod-20261112-gateway-1',60)
            self.nginx.write_text(original);run('nginx','-t');run('nginx','-s','reload');wait_public_ready('https://store.visitworld.me/api/health/ready')
            write_json(self.d/'switch.json',{'release':self.r,'switchedAt':datetime.now(ZoneInfo('Asia/Singapore')).isoformat()})
        except Exception:self.rollback();raise
    def finalize(self):
        assert json.load(open(self.d/'production-browser.json'))['ok'];assert json.load(open(self.d/'production-smoke.json'))['ok']
        old=json.load(open(self.b/'state.before.json'));c=json.load(open(self.compose));before=json.load(open(self.b/'compose.before.json'))
        assert c['services']['worker']==before['services']['worker'];assert self.nginx.read_bytes()==(self.b/'nginx.before.conf').read_bytes();assert Path('/opt/store-next/runtime/runtime.env').read_bytes()==(self.b/'runtime.before.env').read_bytes()
        for n in ('api','worker','gateway'):health('store-next-prod-20261112-'+n+'-1',20)
        now=datetime.now(ZoneInfo('Asia/Singapore')).isoformat();s={**old,'current':self.r,'previous':old['current'],'releaseTag':self.r,'sourceCommit':self.m['sourceCommit'],'apiRelease':self.r,'adminRelease':self.r,'staffRelease':self.r,'workerRelease':old['workerRelease'],'backups':str(self.b),'switchedAt':json.load(open(self.d/'switch.json'))['switchedAt'],'updatedAt':now,'verifiedAt':now,'browserVerification':'attendance restored-database synthetic flow; staff/leader/admin mocked browser and public read-only verification'}
        tmp=self.state.with_suffix('.pending.json');write_json(tmp,s);tmp.replace(self.state);write_json(self.d/'deployment-proof.json',{'ok':True,'release':self.r,'sourceCommit':self.m['sourceCommit'],'rollback':old['current'],'verifiedAt':now,'staffUpdated':True,'workerUnchanged':True,'runtimeUnchanged':True,'migrations':['010-attendance.sql']});self.clean()

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['preflight','stage','artifacts','prepare','candidate','switch','finalize','rollback','clean']);p.add_argument('release');a=p.parse_args();d=Deployment(a.release)
    with open('/opt/store-next/runtime/ui-release.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='stage':
            for phase in ['artifacts','prepare','candidate']:d.phase(phase,getattr(d,phase))
        else:d.phase(a.action,getattr(d,a.action))
        print(json.dumps({'ok':True,'phase':a.action,'release':a.release}),flush=True)
if __name__=='__main__':main()
