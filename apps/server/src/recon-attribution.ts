import type {ReconLayout} from './recon-layout-model';
import type {PoolClient} from 'pg';import {readWorkbookRows} from './visit-workbook';import {monthRange} from './business-dates';
import {reconAttribution,type AttributionKey,type AttributionSource} from '../../../packages/domain/src/recon-attribution';
export async function reconciliationKeys(path:string,month:string,layout?:ReconLayout){
 const headers=new Map<string,{row:number;store:number;date:number}>();
 if(layout)headers.set(layout.sheet,{row:layout.headerRow,store:layout.columns.store,date:layout.columns.date});
 else for await(const row of readWorkbookRows(path)){
  if(row.row>10||headers.has(row.sheet))continue;const values=row.values.map(value=>String(value??'').trim().toLowerCase()),joined=values.join('|');
  if(!['store basic id','store id','店舗id','店铺id'].some(key=>joined.includes(key))||!['agent name','submitter','提交人','员工姓名','姓名'].some(key=>joined.includes(key))||!['statement date','modified time','记录日期'].some(key=>joined.includes(key)))continue;
  const store=values.findIndex(value=>['store basic id','store id','店舗id','店铺id'].some(key=>value.includes(key))),date=values.findIndex(value=>['statement date','modified time','记录日期'].some(key=>value.includes(key)));if(store>=0&&date>=0)headers.set(row.sheet,{row:row.row,store,date});
 }
 const report:AttributionKey[]=[];
 for await(const row of readWorkbookRows(path)){const header=headers.get(row.sheet);if(!header||(layout?row.row<=header.row:row.row<2))continue;const store=String(row.values[header.store]??''),modified=String(row.values[header.date]??'');if(store.trim()&&modified.startsWith(month))report.push({store,modified});}
 return report;
}
export async function attributeReconciliation(client:PoolClient,report:AttributionKey[],month:string){
 const {start,end}=monthRange(month);
 const local=(await client.query<AttributionKey>('SELECT r.store_id_raw AS store,r.modified_raw AS modified FROM raw_records r JOIN formal_records f ON f.raw_record_id=r.id WHERE f.japan_date BETWEEN $1 AND $2 ORDER BY r.id',[start,end])).rows;
 const ids=[...new Set(report.map(row=>row.store))];
 const sources=(await client.query<AttributionSource>("SELECT store_id_raw AS store,modified_raw AS modified,clean_status AS status FROM raw_records WHERE store_id_raw=ANY($1::text[]) AND clean_status<>'visible_blank' ORDER BY id",[ids])).rows;
 return reconAttribution(report,local,sources);
}
