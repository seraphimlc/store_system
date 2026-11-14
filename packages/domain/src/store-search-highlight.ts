import {normalizeStoreQuery,storeQueryTerms} from './store-search';
export interface SearchTextPart {text:string;matched:boolean}
/** Original text slices only. Mapping is grapheme-aware; this never creates HTML or changes search results. */
export function storeSearchTextParts(text:string,query:string,matchedTerms?:readonly string[]):SearchTextPart[]{
 const plain=()=>[{text,matched:false}];
 if(!text||text.length>4096||!query||query.length>200||matchedTerms&&matchedTerms.length>32)return plain();
 const normalized=normalizeStoreQuery(text),needles=[...new Set(matchedTerms===undefined?[normalizeStoreQuery(query),...storeQueryTerms(query)]:matchedTerms.filter(term=>term.length<=200).map(normalizeStoreQuery))].filter(Boolean);
 if(!normalized||!needles.length)return plain();
 const map:{start:number;end:number}[]=[];
 for(const segment of new Intl.Segmenter(undefined,{granularity:'grapheme'}).segment(text)){
  const key=normalizeStoreQuery(segment.segment),range={start:segment.index,end:segment.index+segment.segment.length};
  for(let i=0;i<key.length;i++)map.push(range);
 }
 // Unicode case/composition rules may change lengths in context; a safe plain rendering beats a wrong slice.
 if(map.length!==normalized.length)return plain();
 const ranges:{start:number;end:number}[]=[];
 for(const needle of needles){let from=0;while(from<normalized.length){const at=normalized.indexOf(needle,from);if(at<0)break;
  const first=map[at],last=map[at+needle.length-1];if(first&&last)ranges.push({start:first.start,end:last.end});from=at+1;
 }}
 if(!ranges.length)return plain();ranges.sort((a,b)=>a.start-b.start||a.end-b.end);
 const merged:{start:number;end:number}[]=[];for(const range of ranges){const previous=merged.at(-1);if(previous&&range.start<=previous.end)previous.end=Math.max(previous.end,range.end);else merged.push({...range});}
 const parts:SearchTextPart[]=[];let cursor=0;for(const range of merged){if(range.start>cursor)parts.push({text:text.slice(cursor,range.start),matched:false});parts.push({text:text.slice(range.start,range.end),matched:true});cursor=range.end;}
 if(cursor<text.length)parts.push({text:text.slice(cursor),matched:false});return parts;
}
