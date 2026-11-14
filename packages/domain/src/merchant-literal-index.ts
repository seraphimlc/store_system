import {normalizeStoreQuery,storeQueryTerms} from './store-search';
export interface MerchantLiteralDocument {id:number;storeId:string;name:string;address?:string|null}
/** Immutable normalized field slab: native substring scans, no per-query normalization of the full corpus.
 * NUL cannot occur in PostgreSQL text; separators prevent cross-field matches. No identity merge or result cache.
 */
export class MerchantLiteralIndex {
 private readonly text:string;
 private readonly starts:Uint32Array;
 private readonly ids:Float64Array;
 private readonly nameStarts:Uint32Array;
 private readonly addressStarts:Uint32Array;
 constructor(documents:readonly MerchantLiteralDocument[]){
  const chunks:string[]=[],starts=new Uint32Array(documents.length+1),ids=new Float64Array(documents.length),seen=new Set<number>(),nameStarts=new Uint32Array(documents.length),addressStarts=new Uint32Array(documents.length);let length=0;
  for(let i=0;i<documents.length;i++){
   const d=documents[i];if(!Number.isSafeInteger(d.id)||seen.has(d.id))throw Error('Merchant document IDs must be unique safe integers');seen.add(d.id);
   const fields=[d.storeId,d.name,d.address??''];if(fields.some(field=>field.includes('\0')))throw Error('Merchant source text cannot contain NUL');
   const normalized=fields.map(normalizeStoreQuery),chunk=normalized.join('\0')+'\0';
   nameStarts[i]=length+normalized[0].length+1;addressStarts[i]=nameStarts[i]+normalized[1].length+1;if(length+chunk.length>0xffffffff)throw Error('Merchant literal corpus exceeds slab offset limit');
   starts[i]=length;length+=chunk.length;ids[i]=d.id;chunks.push(chunk);
  }
  starts[documents.length]=length;this.text=chunks.join('');this.starts=starts;this.ids=ids;this.nameStarts=nameStarts;this.addressStarts=addressStarts;
 }
 /** Packed rows are corpus offsets, not source identities; map back only after pagination. */
 searchPacked(query:string):{rows:Uint32Array;tiers:Uint8Array}{
  const q=normalizeStoreQuery(query),terms=storeQueryTerms(query),empty=()=>({rows:new Uint32Array(0),tiers:new Uint8Array(0)});
  if(!q||q.includes('\0')||this.ids.length===0)return empty();
  const expand=terms.length>1||(terms.length===1&&terms[0]!==q),marks=new Uint8Array(this.ids.length);
  const locate=(offset:number,lower:number)=>{let low=lower,high=this.ids.length;while(low+1<high){const mid=(low+high)>>>1;if(this.starts[mid]<=offset)low=mid;else high=mid;}return low;};
  const visit=(term:string,apply:(row:number)=>void)=>{if(!term||term.includes('\0'))return;let lower=0;for(let offset=this.text.indexOf(term);offset>=0;){const row=locate(offset,lower);apply(row);lower=row+1;if(lower>=this.ids.length)break;offset=this.text.indexOf(term,this.starts[lower]);}};
  visit(q,row=>{marks[row]=1;});
  if(expand){
   // Intersection uses compact bit marks rather than one Set per full-corpus term.
   let hits=new Uint8Array(this.ids.length);visit(terms[0],row=>{hits[row]=1;});
   for(let i=1;i<terms.length;i++){const next=new Uint8Array(this.ids.length);visit(terms[i],row=>{if(hits[row])next[row]=1;});hits=next;}
   for(let row=0;row<hits.length;row++)if(hits[row])marks[row]=1;
  }
  let count=0;for(let row=0;row<marks.length;row++)count+=marks[row];
  const rows=new Uint32Array(count),tiers=new Uint8Array(count);let index=0;
  const includes=(term:string,start:number,end:number)=>this.text.slice(start,end).includes(term);
  for(let row=0;row<marks.length;row++)if(marks[row]){
   const start=this.starts[row],name=this.nameStarts[row],address=this.addressStarts[row],end=this.starts[row+1]-1;
   let tier:number;
   if(name-start-1===q.length&&this.text.startsWith(q,start))tier=0;
   else if(address-name-1===q.length&&this.text.startsWith(q,name))tier=1;
   else if(this.text.startsWith(q,name))tier=2;
   else if(includes(q,name,address-1))tier=3;
   else if(expand&&terms.every(term=>includes(term,name,address-1)))tier=4;
   else if(includes(q,start,name-1))tier=5;
   else if(includes(q,address,end))tier=6;
   else tier=7;
   rows[index]=row;tiers[index]=tier;index++;
  }
  return {rows,tiers};
 }
 identity(row:number){if(!Number.isInteger(row)||row<0||row>=this.ids.length)throw Error('Unknown merchant corpus row');return this.ids[row];}
 /** Compatibility adapter; the runtime can use packed rows without allocating one object per broad-query match. */
 search(query:string){const {rows,tiers}=this.searchPacked(query);return Array.from(rows,(row,index)=>({id:this.ids[row],score:0,tier:tiers[index]}));}
}
