/** Frozen-file benchmark only: no DB credentials, source writes, provider calls or deployed service changes. */
import {createReadStream} from 'node:fs';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {createHash} from 'node:crypto';
import {resolve,join} from 'node:path';
import {MerchantLiteralIndex,type MerchantLiteralDocument} from '../packages/domain/src/merchant-literal-index';

const root=resolve(process.argv[2]??'data/refactor/search-evaluation/2026-11-13T12-24-40-840Z-dd3974b9');
if(process.argv.length>3)throw Error('Usage: node --import tsx scripts/benchmark-merchant-literal.mts [frozen-snapshot-directory]');
const manifest=JSON.parse(await readFile(join(root,'manifest.json'),'utf8')),expected=manifest.files.find((f:any)=>f.file==='merchants.jsonl');
if(!expected||expected.rows!==620280)throw Error('Expected fixed 620280-merchant snapshot manifest');
const sourceHash=createHash('sha256'),stream=createReadStream(join(root,'merchants.jsonl'));stream.on('data',chunk=>sourceHash.update(chunk));
let documents:MerchantLiteralDocument[]|null=[],started=performance.now();
for await(const line of createInterface({input:stream,crlfDelay:Infinity})){if(!line)continue;const row=JSON.parse(line);documents.push({id:documents.length+1,storeId:row.place_id,name:row.name??'',address:row.address});}
if(documents.length!==expected.rows||sourceHash.digest('hex')!==expected.sha256)throw Error('Frozen merchant corpus digest/count mismatch');
const loadMs=performance.now()-started;started=performance.now();const index=new MerchantLiteralIndex(documents);documents=null;
const buildMs=performance.now()-started,queries=['chocoZAP','東京都','店','新宿中央薬局','焼肉 中央苑','zzzz完全不存在987654321','长乐药店','新宿','東京 新宿','カフェ 新宿'],samples:any[]=[],timings:number[]=[];
for(const query of queries){started=performance.now();const result=index.searchPacked(query);samples.push({query,ms:performance.now()-started,count:result.rows.length});}
for(let i=0;i<200;i++){started=performance.now();index.searchPacked(queries[i%queries.length]);timings.push(performance.now()-started);}
timings.sort((a,b)=>a-b);
const report={createdAt:new Date().toISOString(),snapshot:manifest.version,sourceSha256:expected.sha256,implementationSha256:createHash('sha256').update(await readFile('packages/domain/src/merchant-literal-index.ts')).digest('hex'),documents:expected.rows,loadMs,buildMs,samples,requests:200,concurrency:1,p95:timings[189],p99:timings[197],rss:process.memoryUsage().rss,databaseAccessed:false,productionAccessed:false,warning:'Literal index core only, not HTTP, facets, authorization, logging or fuzzy retrieval. Not a deployment acceptance or five-concurrent SLO result.'};
const out=resolve('data/refactor/search-evaluation/merchant-packed-'+Date.now());await mkdir(out,{recursive:true,mode:0o700});await writeFile(join(out,'report.json'),JSON.stringify(report,null,2),{mode:0o600});console.log(JSON.stringify({evidence:join(out,'report.json'),...report}));
