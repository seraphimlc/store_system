import test from 'node:test';import assert from 'node:assert/strict';import {spawnSync} from 'node:child_process';
test('full release requires clean full manifest and changes images only for all three services',()=>{const code=`import importlib.util,copy
s=importlib.util.spec_from_file_location('full','scripts/deploy-full.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
r='prod-20990101-000000';x={'id':r,'releaseTag':r,'dirty':False,'sourceCommit':'a'*40,'platform':'linux/amd64','artifactKind':'full-images-v1','migrations':[],'sourceRelease':'previous','staffPwa':[{}],'supportedTaskKinds':['system.check'],'componentScope':{'admin':'rebuild','staff':'rebuild','api':'rebuild','worker':'rebuild-shared-image','gateway':'rebuild'},'images':{'api':'store-next-api:'+r,'gateway':'store-next-gateway:'+r},'imageDigests':{'api':'sha256:'+'a'*64,'gateway':'sha256:'+'b'*64}}
m.validate_manifest(x,r)
for change in [{'dirty':True},{'migrations':['unsafe']},{'platform':'linux/arm64'},{'staffPwa':[]},{'componentScope':{'api':'rebuild'}},{'sourceCommit':'invalid'}]:
 y=copy.deepcopy(x);y.update(change)
 try:m.validate_manifest(y,r)
 except ValueError:pass
 else:raise AssertionError('invalid manifest accepted')
previous={'services':{k:{'image':'old','env_file':['runtime.env'],'volumes':['persistent:/data']} for k in ['api','worker','gateway']},'volumes':{'persistent':{'external':True}}}
z=m.updated_compose(previous,x)
assert previous['services']['worker']['image']=='old'
for k in ['api','worker','gateway']:
 assert z['services'][k]['image']==x['images']['gateway' if k=='gateway' else 'api']
 for f in ['env_file','volumes']:assert z['services'][k][f]==previous['services'][k][f]
assert z['volumes']==previous['volumes']
`;const p=spawnSync('python3',['-c',code],{encoding:'utf8'});assert.equal(p.status,0,p.stderr);});
