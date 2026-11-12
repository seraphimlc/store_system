import {unicodeCasefold} from './unicode-casefold';
export function masterName(value:string){return unicodeCasefold(value.normalize('NFKC')).replace(/[\s\u0085\u001c-\u001f]/g,'');}
/** Python difflib.SequenceMatcher(None,a,b).ratio(), including its default autojunk policy. */
export function sequenceRatio(left:string,right:string){
 const a=[...left],b=[...right],index=new Map<string,number[]>();for(let j=0;j<b.length;j++){const list=index.get(b[j])??[];list.push(j);index.set(b[j],list);}
 if(b.length>=200){const threshold=Math.floor(b.length/100)+1;for(const [key,list]of index)if(list.length>threshold)index.delete(key);}
 const queue:[[number,number,number,number]]|[number,number,number,number][]= [[0,a.length,0,b.length]];let matches=0;
 while(queue.length){const [alo,ahi,blo,bhi]=queue.pop()!;let bestA=alo,bestB=blo,size=0,previous=new Map<number,number>();
  for(let i=alo;i<ahi;i++){const current=new Map<number,number>();for(const j of index.get(a[i])??[]){if(j<blo)continue;if(j>=bhi)break;const length=(previous.get(j-1)??0)+1;current.set(j,length);if(length>size){bestA=i-length+1;bestB=j-length+1;size=length;}}previous=current;}
  while(bestA>alo&&bestB>blo&&a[bestA-1]===b[bestB-1]){bestA--;bestB--;size++;}
  while(bestA+size<ahi&&bestB+size<bhi&&a[bestA+size]===b[bestB+size])size++;
  if(!size)continue;matches+=size;if(alo<bestA&&blo<bestB)queue.push([alo,bestA,blo,bestB]);if(bestA+size<ahi&&bestB+size<bhi)queue.push([bestA+size,ahi,bestB+size,bhi]);
 }
 return a.length+b.length?2*matches/(a.length+b.length):1;
}
export interface MasterEntity {id:number;nameNorm:string;city:string|null;masterId:number}
export interface MasterPair {a:number;b:number;kind:'exact'|'fuzzy';sim:number|null}
export function candidatePairs(entities:MasterEntity[],targets:ReadonlySet<number>,existing:ReadonlySet<string>,threshold=0.9){
 const groups=new Map<string,MasterEntity[]>(),buckets=new Map<string,MasterEntity[]>();const pairs:MasterPair[]=[];const seen=new Set(existing);
 for(const entity of entities){if(entity.masterId!==entity.id||!entity.nameNorm)continue;const group=groups.get(entity.nameNorm)??[];group.push(entity);groups.set(entity.nameNorm,group);const key=JSON.stringify([entity.city||'',[...entity.nameNorm][0],Math.floor([...entity.nameNorm].length/2)]);const bucket=buckets.get(key)??[];bucket.push(entity);buckets.set(key,bucket);}
 const add=(a:MasterEntity,b:MasterEntity,kind:MasterPair['kind'],sim:number|null)=>{const key=`${a.id}:${b.id}`;if(seen.has(key))return;seen.add(key);pairs.push({a:a.id,b:b.id,kind,sim});};
 for(const group of groups.values()){group.sort((a,b)=>a.id-b.id);if(!group.some(entity=>targets.has(entity.id)))continue;for(let i=0;i<group.length;i++)for(let j=i+1;j<group.length;j++)if(targets.has(group[i].id)||targets.has(group[j].id))add(group[i],group[j],'exact',null);}
 for(const bucket of buckets.values()){if(bucket.length<2||bucket.length>500||!bucket.some(entity=>targets.has(entity.id)))continue;bucket.sort((a,b)=>a.id-b.id);for(let i=0;i<bucket.length;i++)for(let j=i+1;j<bucket.length;j++){const a=bucket[i],b=bucket[j];if(!targets.has(a.id)&&!targets.has(b.id)||Math.abs([...a.nameNorm].length-[...b.nameNorm].length)>2||[...a.nameNorm].length<3)continue;const ratio=sequenceRatio(a.nameNorm,b.nameNorm);if(ratio>=threshold)add(a,b,'fuzzy',Math.trunc(ratio*100));}}
 return pairs;
}
