import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {spawnSync} from 'node:child_process';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import ExcelJS from 'exceljs';
import {safeUploadName,privateUploadPath} from '../src/private-files';
import {parseVisitWorkbook,rulesFromText,validModified,parseSubmitter,validStoreAddress,type ParsedVisit} from '../src/visit-workbook';
test('streaming workbook parser matches legacy rows for every committed visit fixture, including broken dimensions',async()=>{
 for(const name of ['wide50.xlsx','flat9_record_id.xlsx','flat9_no_record_id.xlsx','yan_like.xlsx','broken_dimension.xlsx']){
  const path=join('legacy/tests/fixtures',name),rows:ParsedVisit[]=[];const result=await parseVisitWorkbook(path,async row=>{rows.push(row);},undefined,{defaults:false});
  const code="import json,sys\nfrom dataclasses import asdict\nfrom store_settle.loader import load_workbook\nx=load_workbook(sys.argv[1])\nprint(json.dumps({'failed':x.failed,'errors':x.errors,'format':x.format,'parsed_sheets':x.parsed_sheets,'header_row':x.header_row,'data_start_row':x.data_start_row,'total_rows':x.total_rows,'parsed_rows':x.parsed_rows,'rows':[{k:v for k,v in asdict(r).items() if k!='import_id'} for r in x.rows]},default=str,ensure_ascii=False))";
  const oracle=spawnLegacyPython(['-c',code,path],{encoding:'utf8',maxBuffer:8*1024*1024});assert.equal(oracle.status,0,name);const old=JSON.parse(oracle.stdout);assert.equal(old.failed,false,JSON.stringify(old.errors));
  assert.deepEqual({format:result.format,parsed_sheets:result.parsed_sheets,header_row:result.header_row,data_start_row:result.data_start_row,total_rows:result.total_rows,parsed_rows:result.parsed_rows,rows},Object.fromEntries(Object.entries(old).filter(([key])=>!['failed','errors'].includes(key))),name);
 }
});
test('manual layout, rule-sheet priority, unknown submitters and conservative address extraction are preserved',async()=>{
 assert.ok(validModified('2026-11-10 19:20:30'));assert.equal(validModified('2026-02-30 19:20:30'),false);assert.equal(validModified('2026-11-10 25:00:00'),false);assert.deepEqual(parseSubmitter('王小明（００１２）'),{name:'王小明',code:'0012'});
 assert.ok(validStoreAddress('東京都新宿区西新宿2-8-1','テスト店'));assert.equal(validStoreAddress('東京都'),false);assert.equal(validStoreAddress('東京都新宿区西新宿2-8-1','東京都新宿区西新宿2-8-1'),false);
 const lines=['AUDIT_SUCCESS YES 计2点','AUDIT_SUCCESS NO 或空白计1点','AUDIT_FAILED OTHER 计0点','AUDITING NOT_REQUEST 不计分'];assert.equal(rulesFromText(lines)?.length,9);
 const directory=await mkdtemp(join(tmpdir(),'visit-parser-'));
 try{
  const book=new ExcelJS.Workbook(),sheet=book.addWorksheet('客户原表');sheet.addRow(['标题']);sheet.addRow(['编号','店名','时间','人员','审核','投放','Store Address-Local']);sheet.addRow(['S1','店一','2026-11-10 10:00:00','王小明(0012)','AUDIT_SUCCESS','是','東京都新宿区西新宿2-8-1']);
  const path=join(directory,'custom.xlsx');await book.xlsx.writeFile(path);const rows:ParsedVisit[]=[];
  const result=await parseVisitWorkbook(path,async row=>{rows.push(row);},{header_row:2,cols:{store_id:1,store_name:2,modified_time:3,submitter:4,visible:5,deploy:6},value_map:{deploy:{'是':2}}});assert.equal(result.parsed_rows,1);assert.equal(rows[0].deploy_raw,'YES');assert.equal(rows[0].submitter_code,'0012');
  await assert.rejects(()=>parseVisitWorkbook(path,async()=>{}),error=>(error as {code:string}).code==='HEADER_NOT_FOUND');
  sheet.getCell('C3').value='2026-02-30 10:00:00';await book.xlsx.writeFile(path);await assert.rejects(()=>parseVisitWorkbook(path,async()=>{},{header_row:2,cols:{store_id:1,store_name:2,modified_time:3,submitter:4,visible:5,deploy:6}}),error=>(error as {code:string}).code==='MODIFIED_INVALID');
 }finally{await rm(directory,{recursive:true,force:true});}
});

test('private upload names preserve UTF-8, reject path escape and do not corrupt real Latin characters',()=>{
 const name='巡店原件.xlsx';assert.equal(safeUploadName(Buffer.from(name).toString('latin1')),name);assert.equal(safeUploadName(name),name);assert.equal(safeUploadName('café.xlsx'),'café.xlsx');assert.equal(safeUploadName('../visits.xlsx'),'visits.xlsx');assert.equal(safeUploadName('C:\\private\\visits.xlsx'),'visits.xlsx');assert.throws(()=>privateUploadPath('/etc/passwd'));
});
