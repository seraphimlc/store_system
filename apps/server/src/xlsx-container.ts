import {Open} from 'unzipper';import archiver from 'archiver';import {mkdtemp,rm} from 'node:fs/promises';import {createWriteStream} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';import {finished} from 'node:stream/promises';
/** Metadata must precede worksheets for ExcelJS's streaming reader. Never rewrites the original upload. */
export async function orderedWorkbook(source:string){
 const entries=await Open.file(source);
 if(entries.files.length>20000)throw new Error('WORKBOOK_ZIP_ENTRY_LIMIT');
 let total=0;
 for(const entry of entries.files){if(entry.path.startsWith('/')||entry.path.split('/').some(part=>part==='..'))throw new Error('WORKBOOK_ZIP_PATH');total+=entry.uncompressedSize;if(total>512*1024*1024||entry.uncompressedSize>256*1024*1024)throw new Error('WORKBOOK_ZIP_EXPANSION_LIMIT');}
 if(!entries.files.some(entry=>entry.path==='xl/workbook.xml'))throw new Error('WORKBOOK_ZIP_INVALID');
 const directory=await mkdtemp(join(tmpdir(),'store-xlsx-'));const path=join(directory,'ordered.xlsx');
 try{
  const output=createWriteStream(path,{mode:0o600}),archive=archiver('zip',{zlib:{level:1}});archive.on('error',error=>output.destroy(error));archive.pipe(output);
  const rank=(name:string)=>name==='xl/_rels/workbook.xml.rels'?0:name==='xl/workbook.xml'?1:name==='xl/styles.xml'?2:name==='xl/sharedStrings.xml'?3:name.startsWith('xl/worksheets/')?6:4;
  const sorted=entries.files.filter(entry=>entry.type==='File').sort((a,b)=>rank(a.path)-rank(b.path));
  // Wait for each stream to finish being appended. Avoid opening all ZIP entry streams at once.
  for(const entry of sorted){if(entry.path==='xl/_rels/workbook.xml.rels'){if(entry.uncompressedSize>1024*1024)throw new Error('WORKBOOK_RELATIONSHIP_LIMIT');const xml=(await entry.buffer()).toString('utf8').replace(/Target="\/xl\//g,'Target="');archive.append(xml,{name:entry.path});continue;}const stream=entry.stream();archive.append(stream,{name:entry.path});await finished(stream);}
  await archive.finalize();await finished(output);return {path,cleanup:()=>rm(directory,{recursive:true,force:true})};
 }catch(error){await rm(directory,{recursive:true,force:true});throw error;}
}
