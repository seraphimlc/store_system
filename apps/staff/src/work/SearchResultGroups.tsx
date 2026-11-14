import type {Visits} from './types';
export function SearchResultGroups({data,mode,selected,onMode,onSelect,language}:{data:Visits;mode:'grid'|'region';selected:number|null;onMode:(mode:'grid'|'region')=>void;onSelect:(id:number|null)=>void;language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;
 const groups=(mode==='grid'?data.gridGroups:data.regionGroups)??[{id:mode==='grid'?data.grid.id:data.region.id,name:mode==='grid'?data.grid.snapshot.name:data.region.name,count:data.total}];
 return <section className="work-search-groups" aria-label={t('搜索结果分类','検索結果の分類')}>
  <div role="group" aria-label={t('分类方式','分類方法')}><button type="button" aria-pressed={mode==='grid'} onClick={()=>onMode('grid')}>{t('网格','グリッド')}</button><button type="button" aria-pressed={mode==='region'} onClick={()=>onMode('region')}>{t('片区','エリア')}</button></div>
  <div role="group" aria-label={t('结果分类筛选','検索結果の絞り込み')} className="work-search-group-options"><button type="button" aria-pressed={selected===null} onClick={()=>onSelect(null)}>{t('全部','すべて')} {data.scopeMatchedTotal??data.total}</button>{groups.map(group=><p className="work-search-group-name" key={group.id}><button type="button" aria-pressed={selected===group.id} onClick={()=>onSelect(selected===group.id?null:group.id)}>{group.name||'#'+group.id} <span>{group.count} {t('家','件')}</span></button></p>)}</div>
  <p className="work-help">{data.searchScope==='batch'?t('仅搜索本批次内你有查看权限的网格；点击其他网格门店进入其详情。','このバッチ内で閲覧権限のあるグリッドのみ検索します。他のグリッドの店舗はその詳細へ移動します。'):t('当前网格内的搜索结果，不扩大搜索范围','現在のグリッド内の検索結果です。検索範囲は拡大しません。')}</p>
 </section>;
}
