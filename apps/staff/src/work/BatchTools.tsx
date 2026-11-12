import type {Session} from '../../../../packages/contracts/src';
import {BatchEvents} from '../../../../packages/client/src/BatchEvents';
import {BatchProposals} from './BatchProposals';
import {usePageHeader} from '../pageHeader';
import {api} from '../api';
export function BatchTools({id,mode,session,language}:{id:number;mode:'logs'|'new-stores';session:Session;language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 usePageHeader(mode==='logs'?t('批次日志','バッチ履歴'):t('新店核实','新規店舗確認'),'/my/work/batches/'+id);
 return <section className="work-page">{mode==='logs'?<BatchEvents mobile id={id} session={session} language={language} request={api}/>:<BatchProposals id={id} session={session} language={language}/>}</section>;
}
