import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir,writeFile,readFile,rm,symlink} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';import {readUploadPath,privateUploadPath,storeUpload} from '../src/private-files';
test('legacy files resolve only for explicit reads, retain owned upload write boundary and reject symlink/traversal escapes',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'legacy-files-')),owned=join(directory,'owned'),legacy=join(directory,'legacy'),outside=join(directory,'secret.xlsx'),saved={NEXT_UPLOAD_DIRECTORY:process.env.NEXT_UPLOAD_DIRECTORY,NEXT_LEGACY_UPLOAD_DIRECTORY:process.env.NEXT_LEGACY_UPLOAD_DIRECTORY};
 try{
  await mkdir(owned);await mkdir(legacy);await writeFile(join(owned,'new.xlsx'),'new');await writeFile(join(legacy,'old.xlsx'),'old');await writeFile(outside,'private');process.env.NEXT_UPLOAD_DIRECTORY=owned;delete process.env.NEXT_LEGACY_UPLOAD_DIRECTORY;
  assert.equal(await readFile(readUploadPath(join(owned,'new.xlsx')),'utf8'),'new');assert.throws(()=>readUploadPath('/data/uploads/old.xlsx'));process.env.NEXT_LEGACY_UPLOAD_DIRECTORY=legacy;
  assert.equal(await readFile(readUploadPath('/data/uploads/old.xlsx'),'utf8'),'old');assert.throws(()=>privateUploadPath('/data/uploads/old.xlsx'));await assert.rejects(()=>storeUpload('/data/uploads/old.xlsx'));assert.equal(await readFile(join(legacy,'old.xlsx'),'utf8'),'old');
  for(const path of ['/data/uploads/../secret.xlsx','/data/uploads/sub/../../secret.xlsx','/data/uploads/..\\secret.xlsx','/data/uploads','/data/uploads-other/old.xlsx','/etc/passwd','fixture','/data/uploads/missing.xlsx','/data/uploads/old.xlsx\0'])assert.throws(()=>readUploadPath(path));
  await symlink(outside,join(legacy,'escape.xlsx'));await symlink(outside,join(owned,'escape.xlsx'));assert.throws(()=>readUploadPath('/data/uploads/escape.xlsx'));assert.throws(()=>readUploadPath(join(owned,'escape.xlsx')));
  const input=join(owned,'incoming.xlsx');await writeFile(input,'new upload');const stored=await storeUpload(input);assert.equal(await readFile(readUploadPath(stored.path),'utf8'),'new upload');
 }finally{await rm(directory,{recursive:true,force:true});for(const [key,value]of Object.entries(saved))if(value===undefined)delete process.env[key];else process.env[key]=value;}
});

test('historical retained import markers report missing independent original without guessing a filesystem path',()=>{assert.throws(()=>readUploadPath('kept'),error=>(error as Error).message.includes('历史保留数据'));});
