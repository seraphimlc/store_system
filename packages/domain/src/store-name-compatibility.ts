import {oneNameEdit} from './store-similarity';

export const STORE_NAME_COMPATIBILITY_VERSION='name-core-branch-v5';
export function storeNameKey(value:string){return value.normalize('NFKC').toLowerCase().replace(/[\s\p{P}\p{S}]/gu,'').replace(/[\u3041-\u3096]/gu,c=>String.fromCodePoint(c.codePointAt(0)!+0x60));}
const segmenter=new Intl.Segmenter('ja',{granularity:'word'});
// Descriptors only, never aliases. Preserve original source text and never use this for identity merges.
const descriptors=new Set(`cafe coffee bar restaurant dining kitchen shop store rental kimono laundry place coin salon hair clinic beauty hotel studio pharmacy drugstore the and of asian indian chinese japanese カフェ コーヒー バー レストラン ダイニング キッチン ベトナム ラーメン 麻辣湯 モンジャ ケバブ パンケーキ 食堂 薬局 薬店 药店 药局 超市 商店 コンビニ スーパー ドラッグ ストア 創作 居酒屋 中華 料理 焼肉 ホルモン ファッション ヘルス コイン ランドリー 理容 美容 株式会社 有限会社 合同会社 本店 支店 店`.split(' ').map(storeNameKey));
const businessFamilies=[
 ['food','cafe coffee bar restaurant dining kitchen カフェ コーヒー バー レストラン ダイニング キッチン ラーメン 食堂 焼肉 ホルモン 居酒屋 料理 麻辣湯 モンジャ ケバブ パンケーキ'],
 ['personal_care','hair salon beauty 理容 美容'],
 ['health','clinic pharmacy drugstore 薬局 薬店 药店 药局'],
 ['lodging','hotel ホテル'],
 ['laundry','laundry ランドリー コインランドリー'],
 ['apparel_rental','kimono rental'],
].map(([family,words])=>({family,words:new Set(words.split(' ').map(storeNameKey))}));
const branchCue=/^(?:駅前|[東西南北]口|駅|本館|別館)$/u;
export interface StoreNameProfile {key:string;core:string;latin:string[];han:string[];families:string[];branch:string|null}
export type StoreNameCompatibilityReason='empty_name'|'business_family_conflict'|'name_number_conflict'|'branch_conflict'|'latin_core_conflict'|'mixed_script_location_conflict'|'category_only'|'core_conflict'|'compatible';
function profileStoreName(name:string,keyOf:(value:string)=>string):StoreNameProfile {
 const normalized=name.normalize('NFKC').toLowerCase(),words=[...segmenter.segment(normalized)].filter(s=>s.isWordLike).map(s=>s.segment);
 const chunks=normalized.split(/[\s()[\]（）【】/／]+/u).filter(Boolean);
 let branch:string|null=null;
 const lastBranch=[...chunks.slice(1)].reverse().find(s=>s.endsWith('店'));
 if(lastBranch&&chunks.length>1&&lastBranch!==normalized&&!/^(?:本店|支店|店)$/u.test(lastBranch)&&[...lastBranch].length<=20)branch=storeNameKey(lastBranch);
 if(!branch){const ending=chunks.map(chunk=>chunk.match(/[a-z0-9\p{Script=Katakana}ー]([\p{Script=Han}\p{N}丁目東西南北口駅前]+店)$/u)).find(Boolean);if(ending)branch=storeNameKey(ending[1]);}
 if(!branch&&words.at(-1)==='店'&&branchCue.test(words.at(-2)??'')){
  let start=words.length-2;while(start>0&&branchCue.test(words[start]))start--;
  branch=storeNameKey(words.slice(start).join(''));
 }
 const keyed=words.map(word=>({word,key:keyOf(word)})),hasCare=words.some(word=>word==='理容'||word==='美容');
 const families=businessFamilies.filter(family=>keyed.some(({key})=>family.words.has(key))).map(family=>family.family);
 const coreWords=keyed.filter(({word,key})=>!descriptors.has(key)&&!(word==='室'&&hasCare));
 let core=coreWords.map(({key})=>key).join('');if(branch)core=core.replace(branch.replace(/店$/u,''),'').replace(/店$/u,'');
 if(descriptors.has(core)||/^\p{N}+$/u.test(core))core='';
 return {key:keyOf(name),core,families,latin:coreWords.filter(({word})=>/^[a-z]+$/u.test(word)&&word.length>=2).map(({key})=>key),han:coreWords.filter(({word})=>/\p{Script=Han}/u.test(word)&&[...word].length>=2).map(({key})=>key),branch};
}
/** Token-key memoization is bounded and owned by one index build, never by a user query or global scope. */
export function createStoreNameProfiler(){
 const tokens=new Map<string,string>();
 const keyOf=(value:string)=>{const known=tokens.get(value);if(known!==undefined)return known;const key=storeNameKey(value);if(tokens.size<20000&&value.length<=64)tokens.set(value,key);return key;};
 return (name:string)=>profileStoreName(name,keyOf);
}
export function storeNameProfile(name:string):StoreNameProfile{return profileStoreName(name,storeNameKey);}
/** A rejection gate after retrieval, not a score, a synonym dictionary or proof of same entity. */
export function storeNameCompatibility(query:StoreNameProfile,candidate:StoreNameProfile):StoreNameCompatibilityReason {
 if(!query.key||!candidate.key)return 'empty_name';
 if(query.key===candidate.key)return 'compatible';
 const leftNumbers=query.key.match(/[0-9]+/gu),rightNumbers=candidate.key.match(/[0-9]+/gu);
 if(leftNumbers&&rightNumbers&&leftNumbers.join('/')!==rightNumbers.join('/'))return 'name_number_conflict';
 if(query.branch&&candidate.branch&&query.branch!==candidate.branch)return 'branch_conflict';
 if(!query.core||!candidate.core)return 'category_only';
 if(query.families.length&&candidate.families.length&&!query.families.some(family=>candidate.families.includes(family)))return 'business_family_conflict';
 // A shared explicit core lets us recognize a compactly written branch suffix without a location dictionary.
 for(const [withBranch,other] of [[query,candidate],[candidate,query]] as const){
  if(!withBranch.branch||other.branch||!withBranch.core)continue;const at=other.key.indexOf(withBranch.core);
  if(at<0)continue;const tail=other.key.slice(at+withBranch.core.length);
  if(tail.endsWith('店')&&tail.length>1&&tail!==withBranch.branch)return 'branch_conflict';
 }
 // Distinct Latin brand words may not be replaced by a shared service description.
 const missingLatin=query.latin.filter(word=>!candidate.key.includes(word)&&!candidate.latin.some(other=>word.length>=5&&other.length>=5&&oneNameEdit(word,other)!==null));
 if(missingLatin.length&&candidate.latin.length)return 'latin_core_conflict';
 // A shared Latin chain name with conflicting written Han location words is still a different branch.
 if(query.latin.length&&candidate.latin.length&&query.han.length&&candidate.han.length&&!query.han.some(word=>candidate.han.includes(word)))return 'mixed_script_location_conflict';
 let left=query.core,right=candidate.core;
 for(const branch of [query.branch,candidate.branch])if(branch){const stem=branch.replace(/店$/u,'');left=left.replace(stem,'').replace(/店$/u,'');right=right.replace(stem,'').replace(/店$/u,'');}
 if(!left||!right)return 'category_only';
 const shorter=left.length<=right.length?left:right,longer=left.length>right.length?left:right;
 if([...shorter].length>=2&&longer.includes(shorter)&&[...shorter].length/[...longer].length>=.45)return 'compatible';
 if([...left].length>=5&&[...right].length>=5&&oneNameEdit(left,right)!==null)return 'compatible';
 return 'core_conflict';
}
