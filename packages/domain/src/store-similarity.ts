import {normalizeStoreQuery,storeQueryTerms} from './store-search';
export const STORE_SUGGESTION_VERSION='name-edit-one-v1';
export const STORE_SUGGESTION_SCOPE_LIMIT=5000;
export function canSuggestStoreName(query:string){const q=normalizeStoreQuery(query),length=[...q].length;return length>=5&&length<=64&&storeQueryTerms(query).length===1&&/^[\p{L}\p{M}ー]+$/u.test(q);}
/** Whole-name distance of at most one insertion/deletion/substitution, in Unicode code points. */
export function oneNameEdit(a:string,b:string):0|1|null{
 const left=[...a],right=[...b];if(Math.abs(left.length-right.length)>1)return null;
 let i=0,j=0,edits=0;
 while(i<left.length&&j<right.length){if(left[i]===right[j]){i++;j++;continue;}if(++edits>1)return null;if(left.length>=right.length)i++;if(right.length>=left.length)j++;}
 edits+=(i<left.length||j<right.length)?1:0;return edits<=1?edits as 0|1:null;
}
export function groupStoreNameSuggestions(query:string,rows:{id:number;name:string|null}[]){
 if(!canSuggestStoreName(query))return [];
 const q=normalizeStoreQuery(query),groups=new Map<string,{name:string;candidateCount:number;firstTaskId:number}>();
 for(const row of rows){if(typeof row.name!=='string')continue;const key=normalizeStoreQuery(row.name);if(!/^[\p{L}\p{M}ー]+$/u.test(key)||[...key].length>65||oneNameEdit(q,key)!==1)continue;
  const previous=groups.get(key);if(previous){previous.candidateCount++;if(row.id<previous.firstTaskId){previous.name=row.name;previous.firstTaskId=row.id;}}else groups.set(key,{name:row.name,candidateCount:1,firstTaskId:row.id});
 }
 return [...groups.values()].sort((a,b)=>a.firstTaskId-b.firstTaskId);
}
