import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir,mkdtemp,rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {connectorBase,connectorFiles,packageConnector} from './package-mcp-connector.mjs';
const root=new URL('../deploy-next/connector/visit-settle/',import.meta.url);
test('connector origin permits HTTPS/local HTTP without credentials, paths or secrets',()=>{
 for(const origin of ['http://127.0.0.1:3000','http://localhost:8000/','https://store.example'])assert.equal(new URL(connectorBase(origin)).pathname,'/');
 for(const origin of ['http://remote.example','https://user:pass@store.example','https://store.example/mcp','https://store.example/?token=secret','https://store.example/#fragment','file:///tmp'])assert.throws(()=>connectorBase(origin));
});
test('connector Skill documents exactly the 27 source-registered tools and required workflow guards',async()=>{
 const skill=await readFile(new URL('skills/visit-settle/SKILL.md',root),'utf8');assert.match(skill,/^---\nname: visit-settle\ndescription: .+\n---/);
 const rows=[...skill.matchAll(/^\| `(visit_\w+)` \|/gm)].map(match=>match[1]);
 const paths=await readdir(new URL('../apps/server/src/',import.meta.url));const registered=[];
 for(const name of paths.filter(name=>/^mcp.*\.ts$/.test(name)))registered.push(...[...(await readFile(new URL('../apps/server/src/'+name,import.meta.url),'utf8')).matchAll(/\bname:\s*'(visit_\w+)'/g)].map(match=>match[1]));
 assert.equal(rows.length,27);assert.equal(new Set(rows).size,27);assert.deepEqual([...rows].sort(),registered.sort());
 for(const marker of ['员工可发现 6','队长可发现 10','全部 27','JPY','null','pay_plan','expected_status','include_version','preview_id','plan_token','NEED_FILE_KIND','pipeline_ok','ledger.registered/reused','download_url','Bearer','不可信数据'])assert.ok(skill.includes(marker),marker);
 const config=JSON.parse(await readFile(new URL('mcp.json',root),'utf8')),oauth=JSON.parse(await readFile(new URL('oauth-info.json',root),'utf8')),meta=JSON.parse(await readFile(new URL('connector-meta.json',root),'utf8'));
 assert.equal(config.mcpServers['visit-settle'].url,'${VISIT_BASE_URL}/mcp');assert.equal(config.mcpServers['visit-settle'].type,'streamableHttp');assert.equal(meta.auth_mode,'oauth');assert.equal(meta.icon,'icon.svg');assert.equal(oauth.flow,'authorization_code + PKCE(S256)');
 for(const target of Object.values(oauth.discovery))assert.ok(target.startsWith('${VISIT_BASE_URL}/.well-known/'));
 assert.ok((await readFile(new URL('icon.svg',root),'utf8')).includes('<svg'));
});
test('connector ZIP contains only declared resources and substitutes one source origin without credentials',async()=>{
 const require=createRequire(new URL('../apps/server/package.json',import.meta.url)),{Open}=require('unzipper'),dir=await mkdtemp(join(tmpdir(),'mcp-connector-'));
 try{const output=join(dir,'visit-settle.zip');const packaged=await packageConnector('http://127.0.0.1:3000',output);assert.equal(packaged.baseUrl,'http://127.0.0.1:3000');const archive=await Open.file(output);assert.deepEqual(archive.files.map(file=>file.path).sort(),connectorFiles.map(name=>'visit-settle/'+name).sort());
  for(const file of archive.files){assert.ok(!file.path.includes('..'));if(file.path.endsWith('.json')){const text=(await file.buffer()).toString('utf8');assert.ok(!text.includes('${VISIT_BASE_URL}'));JSON.parse(text);assert.ok(!/authorization\s*"\s*:|access_token\s*"\s*:|password\s*"\s*:/i.test(text));}}
  const config=JSON.parse((await archive.files.find(file=>file.path.endsWith('/mcp.json')).buffer()).toString('utf8'));assert.equal(config.mcpServers['visit-settle'].url,'http://127.0.0.1:3000/mcp');
 }finally{await rm(dir,{recursive:true,force:true});}
});
