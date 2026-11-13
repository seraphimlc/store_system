/** Read-only local file availability audit; outputs counts, never filenames or record contents. */
import {createRequire} from 'node:module';import {stat,realpath,mkdir,writeFile} from 'node:fs/promises';import {isAbsolute,resolve,relative} from 'node:path';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url)),{Pool}=require('pg'),url=new URL(process.env.NEXT_TEST_DATABASE_URL??'');
if(!['127.0.0.1','localhost'].includes(url.hostname)||url.pathname!=='/store_settle')throw Error('Explicit local connection required');url.pathname='/store_settle';url.searchParams.set('options','-c default_transaction_read_only=on');const db=new Pool({connectionString:url.href});
const report={capturedAt:new Date().toISOString(),database:'store_settle',readOnly:false,total:0,legacyReferences:0,legacyAvailable:0,legacyMissing:0,otherReferences:0,otherAvailableAtOriginalPath:0,mappedDirectoryConfigured:!!process.env.NEXT_LEGACY_UPLOAD_HOST_DIRECTORY};
try{
 report.readOnly=(await db.query("SELECT current_setting('default_transaction_read_only') AS mode")).rows[0].mode==='on';if(!report.readOnly)throw Error('Read-only required');
 const rows=(await db.query("SELECT stored_path AS path FROM imports UNION ALL SELECT params->>'path' AS path FROM recon_tasks WHERE params->>'path' IS NOT NULL")).rows;
 for(const {path}of rows){report.total++;if(typeof path==='string'&&path.startsWith('/data/uploads/')&&!path.split(/[\\/]/).includes('..')){
  report.legacyReferences++;const host=process.env.NEXT_LEGACY_UPLOAD_HOST_DIRECTORY;let available=false;
  if(host&&isAbsolute(host)&&resolve(host)!=='/')try{const root=await realpath(host),file=await realpath(resolve(root,path.slice('/data/uploads/'.length))),rel=relative(root,file);available=!!rel&&!rel.startsWith('../')&&rel!=='..'&&!isAbsolute(rel)&&(await stat(file)).isFile();}catch{}
  if(available)report.legacyAvailable++;else report.legacyMissing++;
 }else{report.otherReferences++;if(path)try{if((await stat(resolve(path))).isFile())report.otherAvailableAtOriginalPath++;}catch{}}
 }
 await mkdir('data/refactor',{recursive:true});await writeFile('data/refactor/p4-legacy-files.json',JSON.stringify(report,null,2)+'\n',{mode:0o600});console.log(JSON.stringify(report,null,2));
}finally{await db.end();}
