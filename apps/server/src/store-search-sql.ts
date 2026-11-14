import {storeSearchSql} from '../../../packages/domain/src/store-search';
/** Static SQL only. Use bound parameters for query values. */
export function storeSearchColumns(id:string,name:string,address:string,query:string){const key=(field:string)=>`CASE WHEN ${query}='' THEN '' ELSE ${storeSearchSql(field)} END`;return `${key(id)} AS search_id,${key(name)} AS search_name,${key(address)} AS search_address`;}
const terms='(SELECT terms FROM search_input)';
const expandable=(query:string)=>`(cardinality(${terms})>1 OR (cardinality(${terms})=1 AND (${terms})[1]<>${query}))`;
const allTermsInName=(query:string)=>`${expandable(query)} AND NOT EXISTS(SELECT 1 FROM unnest(${terms}) AS term WHERE strpos(search_name,term)=0)`;
const allTermsInFields=(query:string)=>`${expandable(query)} AND NOT EXISTS(SELECT 1 FROM unnest(${terms}) AS term WHERE strpos(search_id,term)=0 AND strpos(search_name,term)=0 AND strpos(search_address,term)=0)`;
export function storeSearchMatch(query:string){return `(${query}='' OR strpos(search_id,${query})>0 OR strpos(search_name,${query})>0 OR strpos(search_address,${query})>0 OR (${allTermsInFields(query)}))`;}
export function storeSearchTier(query:string){return `CASE WHEN ${query}='' OR search_id=${query} THEN 0 WHEN search_name=${query} THEN 1 WHEN starts_with(search_name,${query}) THEN 2 WHEN strpos(search_name,${query})>0 THEN 3 WHEN ${allTermsInName(query)} THEN 4 WHEN strpos(search_id,${query})>0 THEN 5 WHEN strpos(search_address,${query})>0 THEN 6 ELSE 7 END`;}
