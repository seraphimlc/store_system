import {Fragment} from 'react';
import {storeSearchTextParts} from '../../../../packages/domain/src/store-search-highlight';
/** All content is escaped by React, including candidate/source names that look like markup. */
export function SearchText({text,query,terms}:{text:string;query:string;terms?:readonly string[]}){
 return <>{storeSearchTextParts(text,query,terms).map((part,index)=>part.matched?<mark className="work-search-hit" key={index}>{part.text}</mark>:<Fragment key={index}>{part.text}</Fragment>)}</>;
}
