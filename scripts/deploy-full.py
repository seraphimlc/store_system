"""Committed full-image production release; encrypted OSS, restored candidate, one Worker, rollback.

No schema migrations. Existing runtime configuration, volumes and task snapshots are preserved.
Run stages in order under the deployment lock; public acceptance gates finalization.
"""
import argparse,fcntl,importlib.util,json,os,re,shutil,subprocess,time,urllib.request,zipfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
spec=importlib.util.spec_from_file_location('release_common',Path(__file__).with_name('deploy-incremental.py'))
common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common)
run,out,inspect,health,sha,write_json=common.run,common.out,common.inspect,common.health,common.sha,common.write_json

def validate_manifest(m,release):
    if not re.fullmatch(r'prod-\d{8}-\d{6}',release):raise ValueError('release id')
    if m.get('id')!=release or m.get('releaseTag')!=release or m.get('dirty') is not False:raise ValueError('tagged clean release required')
    if not re.fullmatch('[0-9a-f]{40}',m.get('sourceCommit','')):raise ValueError('source commit')
    if m.get('platform')!='linux/amd64' or m.get('artifactKind')!='full-images-v1' or m.get('migrations')!=[]:raise ValueError('full amd64 no-migration manifest required')
    if set(m.get('images',{}))!={'api','gateway'}:raise ValueError('full image inventory')
    for k,prefix in [('api','store-next-api:'),('gateway','store-next-gateway:')]:
        if m['images'][k]!=prefix+release or not re.fullmatch('sha256:[0-9a-f]{64}',m.get('imageDigests',{}).get(k,'')):raise ValueError('image identity')
    if not m.get('staffPwa') or not m.get('supportedTaskKinds') or not m.get('sourceRelease'):raise ValueError('missing full release metadata')
    if m.get('componentScope')!={'admin':'rebuild','staff':'rebuild','api':'rebuild','worker':'rebuild-shared-image','gateway':'rebuild'}:raise ValueError('all components must be released')

def updated_compose(previous,m):
    result=json.loads(json.dumps(previous))
    for name in ('api','worker','gateway'):result['services'][name]['image']=m['images']['gateway' if name=='gateway' else 'api']
    return result

class FullDeployment(common.Deployment):
    def baseline(self):
        validate_manifest(self.m,self.r);s=json.load(open(self.state))
        if s['current']!=self.m['sourceRelease']:raise RuntimeError('production baseline changed')
        return s
    def artifacts(self):
        receipt=json.load(open(self.d/'encrypted-upload.json'));p=self.d/receipt['file']
        if p.name!=receipt['file'] or receipt['release']!=self.r:raise ValueError('upload receipt')
        with urllib.request.urlopen(receipt['url'],timeout=60) as response,open(p,'wb') as f:shutil.copyfileobj(response,f)
        if p.stat().st_size!=receipt['size'] or sha(p)!=receipt['sha256']:raise RuntimeError('ciphertext identity')
        with zipfile.ZipFile(p) as z:
            if sorted(z.namelist())!=sorted(['images.tar.gz.enc','manifest.json','SHA256SUMS']) or z.testzip() is not None:raise ValueError('ZIP whitelist')
            z.extractall(self.d)
        self.m=json.load(open(self.d/'manifest.json'));self.baseline()
        run('sha256sum','--check','SHA256SUMS',cwd=self.d,stdout=subprocess.DEVNULL)
        # Use the currently deployed runtime only to authenticate/decrypt the image archive.
        old=json.load(open(self.compose))['services']['api']['image']
        run('docker','run','--rm','--network','none','--user','0','-v',str(self.d)+':/release','-w','/release',old,'node','--input-type=module','-e',"import{readFile}from'node:fs/promises';import{decryptArchive}from'./encrypted-release-bundle.mjs';await decryptArchive('images.tar.gz.enc','images.tar.gz','decrypt-key.json',JSON.parse(await readFile('manifest.json','utf8')).encryption)")
        run('docker','load','-i',str(self.d/'images.tar.gz'),stdout=subprocess.DEVNULL)
        for k,tag in self.m['images'].items():
            image=inspect(tag)
            if image['Id']!=self.m['imageDigests'][k] or image['Architecture']!='amd64':raise RuntimeError('loaded image mismatch')
        (self.d/'decrypt-key.json').unlink()
        write_json(self.d/'artifacts.json',{'ok':True,'images':self.m['imageDigests']})
    def prepare(self):
        for name in ('ui-candidate-api','ui-candidate-gateway','full-candidate-worker'):
            if subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:raise RuntimeError('candidate name already in use: '+name)
        if (self.b/'state.before.json').exists():raise RuntimeError('backup already exists; do not repeat prepare')
        super().prepare()
    def smoke(self,container,mode):
        run('docker','cp',str(self.d/'full-release-smoke.cjs'),container+':/tmp/full-release-smoke.cjs')
        run('docker','exec','--user','0',container,'chown','node:node','/tmp/full-release-smoke.cjs')
        with open(self.d/(mode+'-smoke.json'),'w') as f:run('docker','exec',container,'node','/tmp/full-release-smoke.cjs',stdout=f)
    def candidate(self):
        super().candidate()
        # Exercise the new Worker only on an isolated empty queue and restored database.
        run('docker','run','-d','--name','full-candidate-worker','--network','deploy_default','--env-file',str(self.d/'candidate.env'),self.m['images']['api'],'node','apps/worker/dist/apps/worker/src/main.js',stdout=subprocess.DEVNULL)
        self.worker_ready('full-candidate-worker','/tmp/ui-jobs')
        run('docker','rm','-f','full-candidate-worker',stdout=subprocess.DEVNULL)
        self.task_probe()
        self.public_assets()
        write_json(self.d/'candidate.json',{'ok':True,'api':True,'gateway':True,'isolatedWorker':True,'taskCompatibility':True})
    def worker_ready(self,name,directory='/data/jobs',seconds=30):
        # Heartbeat path is the same contract used by the production compose healthcheck.
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            state=inspect(name)['State']
            if not state['Running'] or state.get('OOMKilled'):raise RuntimeError('worker stopped')
            probe="const fs=require('fs');const p="+json.dumps(directory+ '/heartbeat')+";if(!fs.existsSync(p)||Date.now()-fs.statSync(p).mtimeMs>30000)process.exit(1)"
            if subprocess.run(['docker','exec',name,'node','-e',probe],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:return
            time.sleep(1)
        raise RuntimeError('worker heartbeat timeout')
    def public_assets(self):
        import hashlib
        for asset in self.m['assets']:
            with urllib.request.urlopen(asset['url'],timeout=15) as response:
                raw=response.read();cors=response.headers.get('Access-Control-Allow-Origin')
                if cors not in ('*','https://store.visitworld.me'):raise RuntimeError('asset CORS')
            if len(raw)!=asset['size'] or hashlib.sha256(raw).hexdigest()!=asset['sha256']:raise RuntimeError('asset digest mismatch')
    def public(self):
        import hashlib
        for url,key in [('https://store.visitworld.me/','index.html'),('https://store.visitworld.me/admin/','admin/index.html')]:
            with urllib.request.urlopen(url,timeout=15) as response:
                if hashlib.sha256(response.read()).hexdigest()!=self.m['html'][key]:raise RuntimeError('public HTML digest mismatch')
        for file in self.m['staffPwa']:
            with urllib.request.urlopen('https://store.visitworld.me/'+file['path'],timeout=15) as response:
                if hashlib.sha256(response.read()).hexdigest()!=file['sha256']:raise RuntimeError('public PWA mismatch')
        common.wait_public_ready('https://store.visitworld.me/api/health/ready')
        write_json(self.d/'public-verification.json',{'ok':True,'html':True,'pwa':True,'assetsCheckedBeforeSwitch':len(self.m['assets']),'health':True})
    def task_probe(self):
        run('docker','run','--rm','--read-only','--network','none','--user','1000:1000','-v','store-next-prod-jobs:/data/jobs:ro','-v',str(self.d/'task-compatibility.cjs')+':/tmp/probe.cjs:ro',self.m['images']['api'],'node','/tmp/probe.cjs')
    def clean(self):
        if subprocess.run(['docker','inspect','full-candidate-worker'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:run('docker','rm','-f','full-candidate-worker',stdout=subprocess.DEVNULL)
        super().clean()
    def switch(self):
        self.baseline()
        for name in ('candidate.json','candidate-smoke.json','local-verification.json'):
            if not json.load(open(self.d/name))['ok']:raise RuntimeError('acceptance failed: '+name)
        if self.compose.read_bytes()!=(self.b/'compose.before.json').read_bytes() or self.nginx.read_bytes()!=(self.b/'nginx.before.conf').read_bytes():raise RuntimeError('runtime changed since prepare')
        self.task_probe();self.clean()
        original=self.nginx.read_text();marker='    location / {\n        proxy_pass http://127.0.0.1:8000;'
        if original.count(marker)!=1:raise RuntimeError('maintenance location does not match; not changing nginx')
        try:
            self.nginx.write_text(original.replace(marker,'    location / {\n        add_header Retry-After "120" always;\n        return 503;\n        proxy_pass http://127.0.0.1:8000;'));run('nginx','-t');run('nginx','-s','reload');time.sleep(3)
            jobs=Path('/var/lib/docker/volumes/store-next-prod-jobs/_data')
            if list((jobs/'running').glob('*')):raise RuntimeError('running tasks; defer release')
            sql="SELECT count(*) FROM pg_stat_activity WHERE datname='store_settle' AND pid<>pg_backend_pid() AND state='active' AND xact_start IS NOT NULL"
            if out('docker','exec','deploy-db-1','sh','-c','psql -U "$POSTGRES_USER" -d store_settle -Atc "$1"','sh',sql)!='0':raise RuntimeError('active business transaction')
            self.dc('--profile','worker','stop','-t','60','worker','api')
            for name in ('api','worker'):
                state=inspect('store-next-prod-20261112-'+name+'-1')['State']
                if state['Running'] or state['ExitCode']==137:raise RuntimeError('unclean shutdown')
            with open(self.b/'database.final.dump','wb') as f:run('docker','exec','deploy-db-1','sh','-c','pg_dump -U "$POSTGRES_USER" -d store_settle -Fc --no-owner --no-acl',stdout=f)
            os.chmod(self.b/'database.final.dump',0o600)
            with open(self.b/'database.final.dump','rb') as f:run('docker','exec','-i','deploy-db-1','pg_restore','--list',stdin=f,stdout=subprocess.DEVNULL)
            write_json(self.compose,updated_compose(json.load(open(self.b/'compose.before.json')),self.m))
            self.dc('up','-d','--no-deps','api');health('store-next-prod-20261112-api-1');self.smoke('store-next-prod-20261112-api-1','production')
            self.dc('--profile','worker','up','-d','--no-deps','worker');self.worker_ready('store-next-prod-20261112-worker-1')
            self.dc('up','-d','--no-deps','gateway');health('store-next-prod-20261112-gateway-1',60)
            self.nginx.write_text(original);run('nginx','-t');run('nginx','-s','reload');common.wait_public_ready('https://store.visitworld.me/api/health/ready')
            write_json(self.d/'switch.json',{'release':self.r,'switchedAt':datetime.now(ZoneInfo('Asia/Singapore')).isoformat()})
        except Exception:self.rollback();raise
    def finalize(self):
        if not json.load(open(self.d/'public-verification.json'))['ok'] or not json.load(open(self.d/'production-smoke.json'))['ok']:raise RuntimeError('public acceptance missing')
        old=json.load(open(self.b/'state.before.json'));before=json.load(open(self.b/'compose.before.json'));current=json.load(open(self.compose))
        if current!=updated_compose(before,self.m):raise RuntimeError('unexpected runtime changes')
        if self.nginx.read_bytes()!=(self.b/'nginx.before.conf').read_bytes() or Path('/opt/store-next/runtime/runtime.env').read_bytes()!=(self.b/'runtime.before.env').read_bytes():raise RuntimeError('private configuration changed')
        for name in ('api','gateway'):health('store-next-prod-20261112-'+name+'-1',30)
        self.worker_ready('store-next-prod-20261112-worker-1')
        now=datetime.now(ZoneInfo('Asia/Singapore')).isoformat();s={**old,'current':self.r,'previous':old['current'],'releaseTag':self.r,'sourceCommit':self.m['sourceCommit'],'backups':str(self.b),'switchedAt':json.load(open(self.d/'switch.json'))['switchedAt'],'updatedAt':now,'verifiedAt':now,'browserVerification':'isolated source browser regressions; restored candidate read-only grid references; public HTML/PWA/assets/health (no live browser session)'}
        for component in ('api','worker','admin','staff'):s[component+'Release']=self.r
        tmp=self.state.with_suffix('.pending.json');write_json(tmp,s);tmp.replace(self.state)
        write_json(self.d/'deployment-proof.json',{'ok':True,'release':self.r,'sourceCommit':self.m['sourceCommit'],'rollback':old['current'],'allComponents':True,'migrations':[]});self.clean()

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['deploy','artifacts','prepare','candidate','switch','finalize','rollback','clean']);p.add_argument('release');a=p.parse_args();d=FullDeployment(a.release)
    lock=open('/opt/store-next/deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if a.action=='deploy':
        for stage in ('artifacts','prepare','candidate'):
            try:d.phase(stage,getattr(d,stage))
            except Exception:d.clean();raise
        d.phase('switch',d.switch)
        try:d.phase('public',d.public)
        except Exception:d.rollback();raise
        d.phase('finalize',d.finalize)
    else:d.phase(a.action,getattr(d,a.action))
if __name__=='__main__':main()
