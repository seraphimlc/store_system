import {storeNameKey} from './store-name-compatibility';
import type {StoreNameMethod} from './store-name-index';
import {normalizeStoreQuery,storeQueryTerms} from './store-search';

export const STORE_RELATED_VERSION='related-name-words-v1';
import type {StoreSearchMatchEvidence} from '../../contracts/src/store-search';
export type {StoreSearchMatchEvidence} from '../../contracts/src/store-search';
const segmenter=new Intl.Segmenter('ja',{granularity:'word'});
/** Server-side word boundaries, not arbitrary character slices. Related words are NOT exact matches. */
export function storeRelatedNameTerms(query:string):string[]{
 if(query.length>80||!query.trim())return [];
 const terms=[...new Set([...segmenter.segment(query.normalize('NFKC'))]
  .filter(part=>part.isWordLike)
  .map(part=>normalizeStoreQuery(part.segment))
  .filter(term=>[...term].length>=2&&[...term].length<=32&&/^[\p{L}\p{M}]+$/u.test(term)))];
 return terms.length>=2&&terms.length<=12?terms:[];
}
/** Only the field that actually contains a term receives highlight evidence. */
export function storeSearchMatchEvidence(query:string,row:{storeId:string;snapshot:{name?:string;address?:string}},mode:'literal'|'related'):StoreSearchMatchEvidence {
 const terms=mode==='related'?storeRelatedNameTerms(query):[...new Set([normalizeStoreQuery(query),...storeQueryTerms(query)])].filter(Boolean);
 const inField=(field:string|undefined)=>terms.filter(term=>normalizeStoreQuery(field??'').includes(term));
 return {mode,nameTerms:inField(row.snapshot.name),addressTerms:mode==='related'?[]:inField(row.snapshot.address),idTerms:mode==='related'?[]:inField(row.storeId)};
}

/** Scores use shared folded bigrams; expose only original candidate slices responsible for overlap. */
export function storeNameCandidateEvidence(query:string,row:{storeId:string;snapshot:{name?:string;address?:string}},method:StoreNameMethod):StoreSearchMatchEvidence {
 const name=row.snapshot.name??'',queryKey=storeNameKey(query),segments=[...new Intl.Segmenter('ja',{granularity:'grapheme'}).segment(name)],terms=new Set<string>();
 if(method==='folded_name'&&queryKey===storeNameKey(name))terms.add(name);
 else for(let i=0;i<segments.length-1;i++){const term=segments[i].segment+segments[i+1].segment,key=storeNameKey(term);if([...key].length>=2&&queryKey.includes(key))terms.add(term);}
 return {mode:method==='folded_name'?'literal':'related',method,nameTerms:[...terms].slice(0,32),addressTerms:[],idTerms:[]};
}
