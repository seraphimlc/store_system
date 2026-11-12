import type {PoolClient} from 'pg';
export interface VisitQuery {q:string;state:string;businessStatus:string;page:number;lat?:number;lon?:number;focus?:number}
/** The same authorized grid snapshot is used for both the map and the drawer. */
export async function mobileVisits(client:PoolClient,gridId:number,input:VisitQuery,closed:boolean){
 const number=(value:string)=>`CASE WHEN ${value} ~ '^-?[0-9]+([.][0-9]+)?$' THEN (${value})::double precision END`;
 const lat=`coalesce(d.lat,m.lat,${number("s.snapshot->>'lat'")})`,lon=`coalesce(d.lon,m.lon,${number("s.snapshot->>'lon'")})`;
 const distance=`6371000*2*asin(sqrt(least(1,power(sin(radians(lat-$5::double precision)/2),2)+cos(radians(lat))*cos(radians($5::double precision))*power(sin(radians(lon-$6::double precision)/2),2))))`;
 const result=(await client.query(`WITH all_rows AS(SELECT s.id,s.store_id AS "storeId",s.snapshot,s.result,s.status,s.revision,
  coalesce((d.photo_urls)::jsonb,(s.result->'photo_urls')::jsonb,'[]'::jsonb) AS photos,coalesce(d.revision,0) AS "photoRevision",coalesce(d.revision,0) AS "profileRevision",
  ${lat} AS lat,${lon} AS lon,d.google_place_id AS "googlePlaceId",d.place_lookup_state AS "placeLookupState",(m.place_id IS NULL) AS "sourceMissing",
  coalesce((SELECT e.new_value->>'location_accuracy' FROM bd_task_event e JOIN bd_store_task x ON x.id=e.store_task_id WHERE x.store_id=s.store_id AND e.event_type='verify_store_location' ORDER BY e.created_at DESC,e.id DESC LIMIT 1),nullif(m.location_accuracy,''),s.snapshot->>'location_accuracy','pending') AS accuracy
  FROM bd_store_task s LEFT JOIN bd_merchant_detail d ON d.store_id=s.store_id AND NOT $8::boolean
  LEFT JOIN public.kanto_merchants_v20261029 m ON m.place_id=s.store_id WHERE s.grid_task_id=$1),
 matching AS(SELECT * FROM all_rows WHERE ($2='' OR "storeId" ILIKE $2 OR snapshot->>'name' ILIKE $2 OR snapshot->>'address' ILIKE $2)
 AND ($3='' OR status=$3) AND ($4='' OR coalesce(result->>'business_status','normal')=$4)),
 ranked AS(SELECT *,CASE WHEN $5::double precision IS NOT NULL AND lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180 THEN ${distance} END AS "distanceMeters" FROM matching),
 paged AS(SELECT * FROM ranked ORDER BY CASE WHEN id=$9 THEN 0 ELSE 1 END,"distanceMeters" ASC NULLS LAST,id LIMIT 20 OFFSET $7)
 SELECT (SELECT count(*)::int FROM matching) AS total,coalesce((SELECT json_agg(p) FROM paged p),'[]') AS items,
  (SELECT count(*)::int FROM all_rows) AS "gridTotal",
  (SELECT count(*)::int FROM all_rows WHERE $2='' OR "storeId" ILIKE $2 OR snapshot->>'name' ILIKE $2 OR snapshot->>'address' ILIKE $2) AS "keywordMatches",
  (SELECT count(*)::int FROM all_rows WHERE $10<>'' AND (strpos(regexp_replace(lower(normalize(coalesce(snapshot->>'name',''),NFKC)),'[[:space:]　]','','g'),$10)>0 OR strpos(regexp_replace(lower(normalize(coalesce(snapshot->>'address',''),NFKC)),'[[:space:]　]','','g'),$10)>0)) AS "normalizedMatches",
  (SELECT count(*) FILTER(WHERE coalesce(snapshot->>'name','')='')::int FROM all_rows) AS "missingName",
  (SELECT count(*) FILTER(WHERE coalesce(snapshot->>'address','')='')::int FROM all_rows) AS "missingAddress",
  (SELECT count(*) FILTER(WHERE "sourceMissing")::int FROM all_rows) AS "missingSource",
  (SELECT count(*) FILTER(WHERE lat IS NULL OR lon IS NULL)::int FROM all_rows) AS "missingLocation"`,[gridId,input.q?'%'+input.q+'%':'',input.state,input.businessStatus,input.lat??null,input.lon??null,(input.page-1)*20,closed,input.focus??null,input.q.normalize('NFKC').toLowerCase().replace(/\s/g,'')])).rows[0];
 return {...result,page:input.page,pageSize:20,order:input.lat===undefined?'source':'nearest'};
}
