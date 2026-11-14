/** Search-only normalization. Never use these keys to merge merchant identities. */
export const STORE_SEARCH_VERSION = 'terms-nfkc-v2';
export const STORE_NORMALIZER_VERSION = 'nfkc-lower-whitespace-v1';
const whitespace=/[\s\u0085\u001c-\u001f]+/gu;
const sqlWhitespace="U&'[\\0009-\\000D\\0020\\0085\\00A0\\1680\\2000-\\200A\\2028\\2029\\202F\\205F\\3000\\FEFF\\001C-\\001F]+'";
export function normalizeStoreQuery(value:string){return value.normalize('NFKC').toLowerCase().replace(whitespace,'');}
/** Retain all distinct literal terms; do not split punctuation, guess words or discard stopwords. */
export function storeQueryTerms(value:string){return [...new Set(value.normalize('NFKC').toLowerCase().split(whitespace).filter(Boolean))];}
/** Expressions must be static SQL identifiers/expressions, never user input. */
export function storeSearchSql(expression:string){return `regexp_replace(lower(normalize(coalesce(${expression},''),NFKC)),${sqlWhitespace},'','g')`;}
export function storeSearchTermsSql(expression:string){return `ARRAY(SELECT DISTINCT term FROM regexp_split_to_table(lower(normalize(coalesce(${expression},''),NFKC)),${sqlWhitespace}) AS term WHERE term<>'' ORDER BY term)`;}
export function literalLike(value:string){return value.replace(/[/%_]/g,character=>'/'+character);}
/** Lower is better. Geography only breaks ties within the same match tier. */
/** Prepared keys reuse the exact v2 tier rules without normalizing every document for every keystroke. */
export function storeMatchTierForKeys(q:string,terms:readonly string[],store:{storeId:string;name:string;address:string}){
 if(!q)return 0;
 const expand=terms.length>1||(terms.length===1&&terms[0]!==q),id=store.storeId,name=store.name,address=store.address;
 if(id===q)return 0;if(name===q)return 1;if(name.startsWith(q))return 2;if(name.includes(q))return 3;
 if(expand&&terms.every(term=>name.includes(term)))return 4;
 if(id.includes(q))return 5;if(address.includes(q))return 6;
 if(expand&&terms.every(term=>id.includes(term)||name.includes(term)||address.includes(term)))return 7;
 return null;
}
export function storeMatchTier(query:string,store:{storeId:string;name:string;address:string}){
 return storeMatchTierForKeys(normalizeStoreQuery(query),storeQueryTerms(query),{storeId:normalizeStoreQuery(store.storeId),name:normalizeStoreQuery(store.name),address:normalizeStoreQuery(store.address)});
}
