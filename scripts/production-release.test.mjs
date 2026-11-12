import test from'node:test';import assert from'node:assert/strict';import{spawnSync}from'node:child_process';
test('production Nginx location replacement preserves TLS and nested unrelated blocks',()=>{
 const code=`import importlib.util
spec=importlib.util.spec_from_file_location('deployment','deploy-next/production-release.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
source="server { server_name store.visitworld.me;\\n    location /mcp { if ($request_method = POST) { return 200; } proxy_pass http://old; }\\n    location /.well-known/acme-challenge/ { root /keep; }\\n    location / { proxy_pass http://old; }\\n    ssl_certificate /keep.pem; }"
result=m.replace_location(source,'/mcp','return 404;')
assert 'location /mcp { return 404; }' in result
assert 'ssl_certificate /keep.pem;' in result
assert 'location /.well-known/acme-challenge/ { root /keep; }' in result
assert 'location / { proxy_pass http://old; }' in result
assert m.replace_location(source,'/missing','return 404;')==source
print('ok')`;
 const r=spawnSync('./.venv/bin/python',['-c',code],{encoding:'utf8'});assert.equal(r.status,0,r.stderr);assert.equal(r.stdout.trim(),'ok');
});
test('production runtime targets explicit DB, defaults read-only, and forbids local HTTP exceptions',()=>{
 const code=`import importlib.util,tempfile,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('deployment','deploy-next/production-release.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as d:
 m.DIR=Path(d)
 values={'NEXT_DATABASE_URL':'postgresql://fixture:private@db:5432/store_settle','NEXT_COOKIE_SECURE':'true'}
 (m.DIR/'runtime-private.json').write_text(json.dumps(values))
 m.write_runtime('store_next_rehearsal_fixture')
 text=(m.DIR/'runtime.env').read_text();assert 'store_next_rehearsal_fixture' in text and 'default_transaction_read_only' in text
 m.write_runtime('store_settle',False);text=(m.DIR/'runtime.env').read_text();assert 'store_settle' in text and 'default_transaction_read_only' not in text
 values['NEXT_LOCAL_HTTP']='true';(m.DIR/'runtime-private.json').write_text(json.dumps(values))
 try:m.write_runtime('store_settle');raise RuntimeError('local exception accepted')
 except AssertionError:pass
print('ok')`;
 const r=spawnSync('./.venv/bin/python',['-c',code],{encoding:'utf8'});assert.equal(r.status,0,r.stderr);assert.equal(r.stdout.trim(),'ok');
});

test('production HTTPS checks tolerate transient reload failures but require the intended release',()=>{
 const code=`import importlib.util
spec=importlib.util.spec_from_file_location('deployment','deploy-next/production-release.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.RELEASE='prod-20261112';m.DOMAIN='store.visitworld.me';m.time.sleep=lambda _:None
calls=[]
def request(args):
 calls.append(args)
 if len(calls)<3:raise RuntimeError('transient reload')
 return 'HTTP/2 200\\r\\nX-Store-Release: prod-20261112\\r\\n'
m.text=request;assert m.verify_http(True);assert len(calls)==7
print('ok')`;
 const r=spawnSync('./.venv/bin/python',['-c',code],{encoding:'utf8'});assert.equal(r.status,0,r.stderr);assert.equal(r.stdout.trim(),'ok');
});
