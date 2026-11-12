"""Explicit visitworld deployment runbook. Run remotely, never prints runtime secrets.
Usage: python3 production-release.py prepare|candidate|cutover|rollback|status prod-YYYYMMDD
"""
import sys,os,json,subprocess,re,secrets,time,hashlib,shutil,tarfile,fcntl
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

def run(args,check=True,**kwargs):
 r=subprocess.run(args,capture_output=True,**kwargs)
 if check and r.returncode:raise RuntimeError('Command failed: '+str(args[0:3]))
 return r

def text(args):return run(args).stdout.decode().strip()
def docker(*args):return text(['docker',*args])
def compose(*args):return docker('compose','-p',PROJECT,'-f',str(DIR/'compose.json'),*args)
def save(p,value):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,indent=2)+'\n');os.chmod(p,0o600)
def sql(query,db='store_settle'):return docker('exec','deploy-db-1','psql','-XAt','-v','ON_ERROR_STOP=1','-U','store_settle','-d',db,'-c',query)
def fingerprint(db='store_settle'):
 return sql("BEGIN READ ONLY; SELECT json_build_object('users',(SELECT count(*) FROM users),'formal',(SELECT count(*) FROM formal_records),'performance',(SELECT count(*) FROM month_perf_records),'payments',(SELECT count(*) FROM payroll_payments),'batches',(SELECT count(*) FROM bd_main_task),'events',(SELECT count(*) FROM bd_task_event),'passwords',(SELECT md5(coalesce(string_agg(id::text||':'||password_hash,',' ORDER BY id),'')) FROM users)); COMMIT;",db).splitlines()[1]
def backup(name):
 file=BACKUP/(name+'.dump');file.parent.mkdir(parents=True,exist_ok=True);os.chmod(file.parent,0o700)
 with file.open('wb') as out:
  r=subprocess.run(['docker','exec','deploy-db-1','pg_dump','-U','store_settle','-d','store_settle','-Fc','--no-owner','--no-acl'],stdout=out,stderr=subprocess.PIPE)
  if r.returncode:raise RuntimeError('Database backup failed')
 os.chmod(file,0o600)
 return {'file':str(file),'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
def write_runtime(database,readonly=True):
 values=json.loads((DIR/'runtime-private.json').read_text());u=urlsplit(values['NEXT_DATABASE_URL']);q=dict(parse_qsl(u.query));q.pop('options',None)
 if readonly:q['options']='-c default_transaction_read_only=on'
 values['NEXT_DATABASE_URL']=urlunsplit((u.scheme,u.netloc,'/'+database,urlencode(q),''))
 assert values['NEXT_COOKIE_SECURE']=='true' and not any(x in values for x in ['NEXT_LOCAL_HTTP','NEXT_LOCAL_BASELINE'])
 data='\n'.join(k+'='+v for k,v in values.items())+'\n';assert all('\n' not in v and '\r' not in v for v in values.values())
 (DIR/'runtime.env').write_text(data);os.chmod(DIR/'runtime.env',0o600)
def replace_location(config,path,body):
 pattern=r'    location '+re.escape(path)+r'\s*\{';m=re.search(pattern,config)
 if not m:return config
 depth=1;i=m.end()
 while depth:
  if config[i]=='{':depth+=1
  elif config[i]=='}':depth-=1
  i+=1
 return config[:m.start()]+'    location '+path+' { '+body+' }'+config[i:]
def config_write(value):
 tmp=NGINX.with_suffix('.next');tmp.write_text(value);os.replace(tmp,NGINX)
 if run(['nginx','-t'],check=False).returncode:raise RuntimeError('Nginx configuration invalid')
 run(['nginx','-s','reload'])
def probe():
 code=r"""const fs=require('fs'),{Pool}=require('./apps/server/node_modules/pg'),crypto=require('crypto');
(async()=>{const db=new Pool({connectionString:process.env.NEXT_DATABASE_URL});try{const u=(await db.query("SELECT id,password_hash FROM users WHERE role='admin' AND is_active=true AND status='active' AND must_change_password=false ORDER BY id LIMIT 1")).rows[0];if(!u)throw Error('admin unavailable');const p=Buffer.from(JSON.stringify({uid:u.id,csrf:'readonly-production-probe',expires:Math.floor(Date.now()/1000)+300,fingerprint:crypto.createHash('sha256').update(u.password_hash).digest('base64url')})).toString('base64url'),c=p+'.'+crypto.createHmac('sha256',process.env.NEXT_SESSION_SECRET).update(p).digest('base64url');let paths=['/health/ready','/auth/session','/dashboard','/dashboard/overview','/payroll/months','/employees','/reconciliation','/teams','/configuration','/station-assets','/station-tasks','/zone-assets/grids','/zone-assets/regions'];const out=[];for(const path of paths){const r=await fetch('http://127.0.0.1:3000/api'+path,{headers:{cookie:'store_session='+c},signal:AbortSignal.timeout(20000)});if(r.status!==200)throw Error(path+':'+r.status);out.push({path,status:r.status});}const imp=(await db.query("SELECT id FROM imports WHERE stored_path LIKE '/data/uploads/%' ORDER BY id LIMIT 1")).rows[0];if(imp){const r=await fetch('http://127.0.0.1:3000/api/imports/'+imp.id+'/original',{headers:{cookie:'store_session='+c}});if(!r.ok)throw Error('Original unavailable');await r.arrayBuffer();out.push({path:'private original',status:r.status});}console.log(JSON.stringify({ok:true,database:(await db.query('select current_database() as db')).rows[0].db,checks:out}));}finally{await db.end()}})().catch(e=>{console.error(e.message);process.exit(1)})"""
 result=json.loads(docker('exec',PROJECT+'-api-1','node','-e',code));save(DIR/'api-acceptance.json',result);return result

def restore_old():
 for name in ['deploy-web-1','deploy-mcp-1']:
  previous=json.loads((BACKUP/(name+'.json')).read_text())['HostConfig']['RestartPolicy']
  policy=previous['Name'] or 'no'
  if policy=='on-failure' and previous.get('MaximumRetryCount'):policy+=':'+str(previous['MaximumRetryCount'])
  docker('update','--restart='+policy,name)
  run(['docker','unpause',name],check=False)

def check_health():
 for i in range(45):
  r=run(['curl','-fsS','--max-time','3','http://127.0.0.1:8000/api/health/ready'],check=False)
  if not r.returncode:return
  time.sleep(1)
 raise RuntimeError('Candidate readiness timeout')
def verify_http(domain=False):
 base='https://'+DOMAIN if domain else 'http://127.0.0.1:8000'
 for path in ['/','/admin/','/admin/staff-reports/review','/my/work','/my/report']:
  args=['curl','-fsS','--max-time','15','-D','-',base+path]
  if domain:args[1:1]=['--resolve',DOMAIN+':443:127.0.0.1']
  for attempt in range(30):
   try:
    response=text(args)
    if 'X-Store-Release: '+RELEASE in response or 'x-store-release: '+RELEASE in response:break
   except RuntimeError:pass
   if attempt==29:raise RuntimeError('HTTPS reload did not stabilize: '+path)
   time.sleep(1)
 return True

def main():
 global MODE,RELEASE,ROOT,DIR,BACKUP,STATE,NGINX,DOMAIN,PROJECT
 MODE,RELEASE=sys.argv[1:];assert MODE in {'prepare','candidate','cutover','rollback','status'} and re.fullmatch(r'prod-[0-9]{8}(?:-[a-z0-9]+)?',RELEASE)
 ROOT=Path('/opt/store-next');DIR=ROOT/'releases'/RELEASE;BACKUP=ROOT/'backups'/RELEASE;STATE=ROOT/'production-state.json';NGINX=Path('/etc/nginx/conf.d/store.conf');DOMAIN='store.visitworld.me';PROJECT='store-next-'+RELEASE
 assert Path('/opt/store-settle/current').is_dir() and os.geteuid()==0
 if MODE!='status':
  ROOT.mkdir(exist_ok=True)
  deployment_lock=(ROOT/'deploy.lock').open('a')
  fcntl.flock(deployment_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if MODE=='prepare':
  assert not STATE.exists(),'Existing production state requires explicit upgrade procedure'
  DIR.mkdir(parents=True,exist_ok=True);BACKUP.mkdir(parents=True,exist_ok=True);os.chmod(BACKUP,0o700)
  assert not (BACKUP/'preflight.dump').exists(),'Prepare already completed or interrupted; inspect before resuming'
  shutil.copy2(NGINX,BACKUP/'store.conf');os.chmod(BACKUP/'store.conf',0o600)
  for name in ['deploy-web-1','deploy-mcp-1','deploy-db-1']:
   inspect=json.loads(docker('inspect',name))[0];save(BACKUP/(name+'.json'),inspect)
  old=json.loads((BACKUP/'deploy-web-1.json').read_text());env=dict(x.split('=',1) for x in old['Config']['Env']);url=env['DATABASE_URL'].replace('postgresql+psycopg2:','postgresql:');assert urlsplit(url).path=='/store_settle';assert urlsplit(url).hostname=='db'
  values={'NEXT_DATABASE_URL':url,'NEXT_SESSION_SECRET':secrets.token_urlsafe(48),'NEXT_COOKIE_SECURE':'true','NODE_ENV':'production','NEXT_HOST':'0.0.0.0','NEXT_PORT':'3000','NEXT_TASK_DIRECTORY':'/data/jobs','NEXT_UPLOAD_DIRECTORY':'/data/jobs/uploads','NEXT_LEGACY_UPLOAD_DIRECTORY':'/data/legacy-uploads','NEXT_WORKER_RECONCILE':'false'}
  allowed=['GOOGLE_PUBLIC_API_KEY','AI_API_KEY','AI_BASE_URL','AI_MODEL','BONUS_GROUP','BONUS_AMOUNT','BONUS_GROUP_SCHEDULE','BONUS_AMOUNT_SCHEDULE','STAFF_VISIBLE_FROM','DEFAULT_VISIBLE_MAP','DEFAULT_DEPLOY_MAP','DEFAULT_POINT_RULES','DEDUP_SUB_STORES','STORE_AUTO_MERGE_EXACT','STORE_AI_BATCH']
  values.update({k:env[k] for k in allowed if env.get(k)});save(DIR/'runtime-private.json',values)
  pre=fingerprint();info=backup('preflight');db='store_next_rehearsal_'+RELEASE.replace('-','_');sql('CREATE DATABASE "'+db+'"')
  with (BACKUP/'preflight.dump').open('rb') as inp:
   run(['docker','exec','-i','deploy-db-1','pg_restore','-U','store_settle','-d',db,'--no-owner','--no-acl','--exit-on-error'],stdin=inp)
  assert fingerprint(db)==pre,'Backup fingerprint differed; inspect concurrent writes and retake'
  files=BACKUP/'legacy-uploads.tar.gz'
  with tarfile.open(files,'w:gz') as t:t.add('/var/lib/docker/volumes/deploy_store_uploads/_data',arcname='legacy-uploads')
  os.chmod(files,0o600)
  save(BACKUP/'restore-proof.json',{'ok':True,'databaseBackup':info,'restoredDatabase':db,'fingerprint':json.loads(pre),'uploadsBytes':files.stat().st_size,'capturedAt':time.strftime('%Y-%m-%dT%H:%M:%S%z')})
  print(json.dumps({'ok':True,'stage':'backup-restored','databaseBackupBytes':info['bytes'],'restoredDatabase':db,'productionChanged':False}))
 elif MODE=='candidate':
  assert (DIR/'images.tar.gz').is_file() and (BACKUP/'restore-proof.json').is_file()
  proof=json.loads((BACKUP/'restore-proof.json').read_text());db=proof['restoredDatabase'];write_runtime(db,True)
  static=ROOT/'legacy-static'
  if not static.exists():
   docker('cp','deploy-web-1:/srv/app/app/static',str(static));os.chmod(static,0o755)
   for p in static.rglob('*'):os.chmod(p,0o755 if p.is_dir() else 0o644)
  docker('load','-i',str(DIR/'images.tar.gz'))
  images={name:json.loads(docker('image','inspect','store-next-'+name+':'+RELEASE))[0] for name in ['api','gateway']}
  assert all(image['Architecture']=='amd64' for image in images.values())
  save(DIR/'image-identities.json',{name:image['Id'] for name,image in images.items()})
  docker('volume','create','store-next-prod-jobs')
  compose_data={'services':{
  'api':{'image':'store-next-api:'+RELEASE,'env_file':['./runtime.env'],'restart':'unless-stopped','mem_limit':'512m','cpus':1,'networks':['private','database'],'volumes':['jobs:/data/jobs',{'type':'bind','source':'/var/lib/docker/volumes/deploy_store_uploads/_data','target':'/data/legacy-uploads','read_only':True}],'healthcheck':{'test':['CMD','node','-e','fetch("http://127.0.0.1:3000/api/health/ready").then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))'],'interval':'5s','timeout':'3s','retries':12}},
  'gateway':{'image':'store-next-gateway:'+RELEASE,'ports':['127.0.0.1:8000:80'],'networks':['private'],'restart':'unless-stopped','mem_limit':'64m','depends_on':{'api':{'condition':'service_healthy'}}},
  'worker':{'image':'store-next-api:'+RELEASE,'env_file':['./runtime.env'],'profiles':['worker'],'command':['node','apps/worker/dist/apps/worker/src/main.js'],'restart':'unless-stopped','mem_limit':'512m','cpus':1,'networks':['private','database'],'volumes':['jobs:/data/jobs',{'type':'bind','source':'/var/lib/docker/volumes/deploy_store_uploads/_data','target':'/data/legacy-uploads','read_only':True}],'healthcheck':{'test':['CMD','node','-e','const fs=require("fs");process.exit(Date.now()-Number(fs.readFileSync("/data/jobs/heartbeat","utf8"))<10000?0:1)'],'interval':'5s','timeout':'3s','retries':12}}},'volumes':{'jobs':{'external':True,'name':'store-next-prod-jobs'}},'networks':{'private':{},'database':{'external':True,'name':'deploy_default'}}}
  save(DIR/'compose.json',compose_data);docker('run','--rm','--user','0:0','-v','store-next-prod-jobs:/data/jobs','store-next-api:'+RELEASE,'node','-e','require("fs").chownSync("/data/jobs",1000,1000)')
  compose('up','-d','--wait','--wait-timeout','90','api','gateway');check_health();probe();verify_http();save(DIR/'candidate-proof.json',{'ok':True,'db':db,'readonly':True,'workerStarted':False});print(json.dumps({'ok':True,'stage':'candidate','database':db,'readOnly':True,'liveEntryUnchanged':True}))
 elif MODE=='cutover':
  assert (DIR/'candidate-proof.json').exists() and not STATE.exists()
  original=(BACKUP/'store.conf').read_text();assert NGINX.read_text()==original,'Nginx changed since backup'
  maintenance=original.replace('server_name '+DOMAIN+';','server_name '+DOMAIN+';\n    return 503;',1)
  config_write(maintenance)
  try:
   # Wait for in-flight transactions and reconciling tasks; stop old entrypoints without stopping DB.
   for i in range(90):
    active=int(sql("SELECT count(*) FROM pg_stat_activity WHERE datname='store_settle' AND pid<>pg_backend_pid() AND state<>'idle'"))
    pending=int(sql("SELECT count(*) FROM recon_tasks WHERE status IN ('pending','running')"))
    if not active and not pending:break
    time.sleep(1)
   else:raise RuntimeError('Old transactions did not drain')
   # Freeze old processes; unpause is rollback without executing its migration entrypoint.
   docker('pause','deploy-web-1','deploy-mcp-1');docker('update','--restart=no','deploy-web-1','deploy-mcp-1');save(BACKUP/'old-paused.json',{'containers':['deploy-web-1','deploy-mcp-1']})
   assert int(sql("SELECT count(*) FROM pg_stat_activity WHERE datname='store_settle' AND pid<>pg_backend_pid() AND xact_start IS NOT NULL"))==0,'Old transaction remained open after pause; resume and drain before retry'
   final=fingerprint();info=backup('cutover-'+time.strftime('%Y%m%d_%H%M%S'));save(BACKUP/'cutover-proof.json',{'fingerprint':json.loads(final),'backup':info})
   write_runtime('store_settle',True);compose('up','-d','--force-recreate','--wait','--wait-timeout','90','api');check_health();probe();assert fingerprint()==final
   live=original
   for location in ['/mcp','/oauth/','/.well-known/']:live=replace_location(live,location,'default_type application/json; return 404;')
   # All old app locations now point at the candidate; ACME and independent static preview stay intact.
   live=live.replace('proxy_pass http://127.0.0.1:3660;','proxy_pass http://127.0.0.1:8000;')
   live=replace_location(live,'/static/','alias /opt/store-next/legacy-static/; add_header Cache-Control "public, max-age=31536000, immutable";')
   # Keep writes frozen through the public verification pass.
   frozen=live.replace('server_name '+DOMAIN+';','server_name '+DOMAIN+';\n    if ($request_method !~ ^(GET|HEAD|OPTIONS)$) { return 503; }',1)
   config_write(frozen);verify_http(True);probe();assert fingerprint()==final
   write_runtime('store_settle',False);compose('up','-d','--force-recreate','--wait','--wait-timeout','90','api');probe()
   compose('--profile','worker','up','-d','--wait','--wait-timeout','90','worker');config_write(live);verify_http(True)
   save(STATE,{'current':RELEASE,'previous':'legacy-20261111_145314','database':'store_settle','oldPaused':True,'backups':str(BACKUP),'switchedAt':time.strftime('%Y-%m-%dT%H:%M:%S%z')});print(json.dumps({'ok':True,'stage':'production','release':RELEASE,'domain':DOMAIN,'database':'store_settle','oldPaused':True}))
  except Exception:
   recovery=[]
   for step in [lambda:config_write(maintenance),lambda:compose('stop','worker'),restore_old,lambda:config_write(original)]:
    try:step()
    except Exception as error:recovery.append(type(error).__name__)
   save(ROOT/'last-failed-cutover.json',{'release':RELEASE,'restoredOldEntry':not recovery,'recoveryErrors':recovery,'databaseRestored':False})
   raise
 elif MODE=='rollback':
  assert STATE.exists() and json.loads(STATE.read_text())['current']==RELEASE
  current=NGINX.read_text();config_write(current.replace('server_name '+DOMAIN+';','server_name '+DOMAIN+';\n    return 503;',1));compose('stop','worker','api');backup('before-application-rollback');restore_old();config_write((BACKUP/'store.conf').read_text());save(ROOT/'last-rollback.json',{'release':RELEASE,'databaseRestored':False,'legalNewDataPreserved':True,'at':time.strftime('%Y-%m-%dT%H:%M:%S%z')});STATE.rename(ROOT/('production-state-rolled-back-'+RELEASE+'.json'));print(json.dumps({'ok':True,'stage':'rolled-back','databaseRestored':False}))
 elif MODE=='status':
  print(json.dumps({'state':json.loads(STATE.read_text()) if STATE.exists() else None,'candidate':json.loads((DIR/'candidate-proof.json').read_text()) if (DIR/'candidate-proof.json').exists() else None}))

if __name__=='__main__':main()
