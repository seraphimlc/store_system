export interface AttributionKey {store:string;modified:string}
export interface AttributionSource extends AttributionKey {status:string|null}
export function reconAttribution(report:AttributionKey[],formal:AttributionKey[],sources:AttributionSource[]){
 const timestamp=report.some(row=>row.modified.length>=16);const key=(row:AttributionKey)=>JSON.stringify(timestamp?[row.store,row.modified]:[row.store]);
 const local=new Set(formal.map(key)),remote=new Set(report.map(key)),statuses=new Map<string,string|null>();
 for(const row of sources)if(row.status!==null&&row.status!=='visible_blank'&&!statuses.has(key(row)))statuses.set(key(row),row.status);
 const byReason:Record<string,number>={from_sub:0,master_late:0,cross_file_dup:0,no_ref:0,not_found:0};let consistent=0;
 for(const item of local)if(remote.has(item))consistent++;
 for(const item of remote)if(!local.has(item)){const status=statuses.get(item)??'not_found';byReason[status]=(byReason[status]??0)+1;}
 const onlySystem=local.size-consistent,onlyReport=Object.values(byReason).reduce((sum,value)=>sum+value,0);
 return {consistent,only_system:onlySystem,only_report:onlyReport,only_report_reason:byReason,net:onlySystem-onlyReport};
}
