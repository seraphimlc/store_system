import {storeRelatedNameTerms} from './store-search-evidence';
import {normalizeStoreQuery,storeQueryTerms,storeMatchTierForKeys} from './store-search';
import {storeNameKey,storeNameProfile,createStoreNameProfiler,storeNameCompatibility,type StoreNameProfile} from './store-name-compatibility';
import {storeAddressProfile,storeAddressEvidence} from './store-address-evidence';

export const STORE_NAME_INDEX_VERSION='guarded-name-index-v1';
export interface StoreNameDocument {id:number;name:string;address?:string|null;storeId?:string}
export interface StoreNameCandidate {id:number;score:number;tier?:number}
export type StoreNameMethod='folded_name'|'weighted_name'|'exact_core';
export interface StoreNameRetrieval {method:StoreNameMethod|null;candidates:StoreNameCandidate[];status:'ready'|'unsupported_query'|'too_many_candidates'|'no_candidates'}
export function storeNameBigrams(key:string){const chars=[...key];return [...new Set(Array.from({length:Math.max(0,chars.length-1)},(_,i)=>chars.slice(i,i+2).join('')))];}
interface NameGroup {key:string;name:string;rows:StoreNameDocument[];grams:string[];profile?:StoreNameProfile;weight:number;length:number}
/** Immutable corpus index. Duplicates retain their own IDs; no identity merge, DB access or query cache. */
export class StoreNameIndex {
 private groups:NameGroup[]=[];
 private literalRows:{id:number;storeId:string;name:string;address:string}[]=[];
 private documentCount=0;
 private exact=new Map<string,NameGroup>();
 private postings=new Map<string,number[]>();
 private weights=new Map<string,number>();
 private core=new Map<string,NameGroup[]>();
 private overlap:Float64Array;
 private profileName=createStoreNameProfiler();
 private coreReady=false;
 constructor(documents:Iterable<StoreNameDocument>){
  const ids=new Set<number>();
  for(const document of documents){if(!Number.isSafeInteger(document.id)||ids.has(document.id))throw Error('Search document IDs must be unique safe integers');ids.add(document.id);if(document.storeId!==undefined)this.literalRows.push({id:document.id,storeId:normalizeStoreQuery(document.storeId),name:normalizeStoreQuery(document.name),address:normalizeStoreQuery(document.address??'')});const key=storeNameKey(document.name);if(!key)continue;
   let group=this.exact.get(key);if(!group){group={key,name:document.name,rows:[],grams:storeNameBigrams(key),weight:0,length:[...key].length};this.exact.set(key,group);this.groups.push(group);}else if(document.name<group.name)group.name=document.name;
   group.rows.push({...document});
  }
  this.documentCount=ids.size;this.overlap=new Float64Array(this.groups.length);
  this.groups.forEach((group,id)=>{for(const gram of group.grams){let list=this.postings.get(gram);if(!list)this.postings.set(gram,list=[]);list.push(id);}});
  for(const [gram,ids] of this.postings)this.weights.set(gram,1+Math.log((this.groups.length+1)/(ids.length+1)));
  for(const group of this.groups){group.weight=group.grams.reduce((sum,g)=>sum+this.weights.get(g)!,0);group.grams=[];}
 }
 private nameProfile(group:NameGroup){return group.profile??(group.profile=this.profileName(group.name));}
 private ensureCore(){
  if(this.coreReady)return;
  for(const group of this.groups){const profile=this.nameProfile(group);if([...profile.core].length<3)continue;const key=this.coreKey(profile);let values=this.core.get(key);if(!values)this.core.set(key,values=[]);values.push(group);}
  this.coreReady=true;
 }
 private coreKey(profile:StoreNameProfile){return JSON.stringify([profile.core,profile.branch]);}
 literalSearch(query:string):StoreNameCandidate[]{
  const q=normalizeStoreQuery(query),terms=storeQueryTerms(query),matches:StoreNameCandidate[]=[];
  if(!q)return matches;
  for(const row of this.literalRows){const tier=storeMatchTierForKeys(q,terms,row);if(tier!==null)matches.push({id:row.id,score:0,tier});}
  return matches;
 }
 relatedWords(query:string):StoreNameCandidate[]{
  const terms=storeRelatedNameTerms(query);if(!terms.length||this.documentCount>5000)return [];
  const profile=storeNameProfile(query),rejected=new Set(['branch_conflict','name_number_conflict','latin_core_conflict','business_family_conflict','mixed_script_location_conflict']);
  const result:StoreNameCandidate[]=[];
  for(const group of this.groups){if(profile.latin.some(term=>!group.key.includes(term)))continue;const matches=group.rows.filter(row=>terms.some(term=>normalizeStoreQuery(row.name).includes(term)));if(!matches.length||rejected.has(storeNameCompatibility(profile,this.nameProfile(group))))continue;for(const row of matches)result.push({id:row.id,score:0});}
  return result;
 }
 search(query:string,address?:string|null):StoreNameRetrieval {
  const key=storeNameKey(query),exact=this.exact.get(key);
  if(exact)return {method:'folded_name',candidates:exact.rows.map(row=>({id:row.id,score:1})),status:'ready'};
  if(!key||query.length>200||[...key].length<3||/^\p{N}+$/u.test(key))return {method:null,candidates:[],status:'unsupported_query'};
  const queryLength=[...key].length,profile=storeNameProfile(query),queryAddress=storeAddressProfile(address),grams=storeNameBigrams(key),overlap=this.overlap,touched:number[]=[];
  const addressAllowed=(document:StoreNameDocument)=>['unknown','same_written_location'].includes(storeAddressEvidence(queryAddress,storeAddressProfile(document.address)));
  const eligible=(group:NameGroup)=>storeNameCompatibility(profile,this.nameProfile(group))==='compatible';
  let tooMany=false;
  if(queryLength>=4){
   for(const gram of grams){const weight=this.weights.get(gram);if(weight===undefined)continue;for(const id of this.postings.get(gram)!){const group=this.groups[id];if(group.length<4||group.length<queryLength*.45||group.length>queryLength/.45)continue;if(overlap[id]===0)touched.push(id);overlap[id]+=weight;}}
   const total=grams.reduce((sum,g)=>sum+(this.weights.get(g)??(1+Math.log(this.groups.length+1))),0),matches:StoreNameCandidate[]=[];
   for(const id of touched){const weight=overlap[id];overlap[id]=0;const group=this.groups[id],score=2*weight/(total+group.weight);if(score<.6||!eligible(group))continue;for(const row of group.rows)if(addressAllowed(row))matches.push({id:row.id,score});}
   if(matches.length>0&&matches.length<=20)return {method:'weighted_name',candidates:matches.sort((a,b)=>b.score-a.score||a.id-b.id),status:'ready'};
   tooMany=matches.length>20;
  }
  if([...profile.core].length>=3){this.ensureCore();const matches:StoreNameCandidate[]=[];for(const group of this.core.get(this.coreKey(profile))??[]){if(!eligible(group))continue;for(const row of group.rows)if(addressAllowed(row))matches.push({id:row.id,score:1});}
   if(matches.length>0&&matches.length<=20)return {method:'exact_core',candidates:matches.sort((a,b)=>a.id-b.id),status:'ready'};tooMany ||= matches.length>20;
  }
  return {method:null,candidates:[],status:tooMany?'too_many_candidates':'no_candidates'};
 }
}
