/** Lexical address evidence only: no geocoding, translation, inferred coordinates or entity merging. */
export interface StoreAddressProfile {prefecture:string|null;town:string|null;numbers:string[]}
export type StoreAddressEvidence='same_written_location'|'prefecture_conflict'|'town_conflict'|'street_number_conflict'|'unknown';
export function storeAddressProfile(value:string|null|undefined):StoreAddressProfile {
 const text=(value??'').normalize('NFKC').toLowerCase().replace(/(?:〒\s*)?\b\d{3}[-－−]\d{4}\b/gu,'').replace(/(?<=[0-9])\s+(?=[0-9])/gu,'|').replace(/\s/gu,'');
 const prefecture=text.match(/東京都|北海道|大阪府|京都府|[\p{Script=Han}]{2,3}県/u)?.[0]??null;
 const at=text.search(/[0-9]/u),prefix=at>=0?text.slice(0,at):'';
 const townText=prefix.split(/[都道府県市区郡]/u).at(-1)??'';
 // Japanese written locality names only. English/Japanese equivalence remains unknown.
 const town=/^[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}ー]{2,20}$/u.test(townText)?townText:null;
 const street=at>=0?text.slice(at).replace(/丁目|番地?|号/gu,'-').replace(/[‐‑−–—ー－]/gu,'-'):'';
 const match=street.match(/^(\d+)-(\d+)-(\d+)/u);
 return {prefecture,town,numbers:match?match.slice(1).map(n=>String(Number(n))):[]};
}
export function storeAddressEvidence(query:StoreAddressProfile,candidate:StoreAddressProfile):StoreAddressEvidence {
 if(query.prefecture&&candidate.prefecture&&query.prefecture!==candidate.prefecture)return 'prefecture_conflict';
 if(query.town&&candidate.town&&query.town!==candidate.town)return 'town_conflict';
 if(query.town&&candidate.town&&query.town===candidate.town&&query.numbers.length===3&&candidate.numbers.length===3){
  return query.numbers.every((n,i)=>n===candidate.numbers[i])?'same_written_location':'street_number_conflict';
 }
 return 'unknown';
}
