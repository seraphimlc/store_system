export interface MerchantGeographicGroup {id:number;name:string;regionId?:number;regionName?:string}
export interface PackedMerchantMatches {rows:Uint32Array;tiers:Uint8Array}
export interface PackedMerchantLinks {rows:Uint32Array;gridIds:Uint32Array;regionIds:Uint32Array}
/** Geographic source membership only. No task IDs, team metadata, permissions or query cache.
 * The caller must load one complete published geography version, excluding archived/non-ready groups.
 */
export class MerchantFacetIndex {
 private readonly offsets:Uint32Array;
 private readonly grids:Uint32Array;
 private readonly regions:Uint32Array;
 private readonly gridLabels:readonly MerchantGeographicGroup[];
 private readonly regionLabels:readonly MerchantGeographicGroup[];
 private readonly gridLookup:Map<number,number>;
 private readonly regionLookup:Map<number,number>;
 constructor(private readonly merchantCount:number,links:PackedMerchantLinks,gridLabels:readonly MerchantGeographicGroup[],regionLabels:readonly MerchantGeographicGroup[]){
  if(!Number.isInteger(merchantCount)||merchantCount<0||merchantCount>0xffffffff)throw Error('Invalid merchant count');
  if(links.rows.length!==links.gridIds.length||links.rows.length!==links.regionIds.length)throw Error('Geographic column lengths differ');
  const lookup=(labels:readonly MerchantGeographicGroup[])=>{const m=new Map<number,number>();for(let i=0;i<labels.length;i++){if(!Number.isSafeInteger(labels[i].id)||labels[i].id<=0||m.has(labels[i].id))throw Error('Geographic IDs must be positive and unique');m.set(labels[i].id,i);}return m;};
  this.gridLookup=lookup(gridLabels);this.regionLookup=lookup(regionLabels);this.gridLabels=gridLabels.map(x=>({...x}));this.regionLabels=regionLabels.map(x=>({...x}));
  this.offsets=new Uint32Array(merchantCount+1);this.grids=new Uint32Array(links.rows.length);this.regions=new Uint32Array(links.rows.length);
  let previous=-1;
  for(let i=0;i<links.rows.length;i++){
   const row=links.rows[i],grid=this.gridLookup.get(links.gridIds[i]),region=this.regionLookup.get(links.regionIds[i]);
   if(row>=merchantCount||row<previous)throw Error('Geographic rows must be valid ascending corpus offsets');
   if(grid===undefined||region===undefined)throw Error('Unknown geographic group');
   if(gridLabels[grid].regionId!==regionLabels[region].id)throw Error('Grid does not belong to supplied region');
   previous=row;this.offsets[row+1]++;this.grids[i]=grid;this.regions[i]=region;
  }
  for(let row=1;row<this.offsets.length;row++)this.offsets[row]+=this.offsets[row-1];
 }
 select(matches:PackedMerchantMatches,input:{page?:number;pageSize?:number;groupGrid?:number;groupRegion?:number}={}){
  const page=input.page??1,pageSize=input.pageSize??20;
  if(!Number.isSafeInteger(page)||page<1||!Number.isSafeInteger(pageSize)||pageSize<1||pageSize>100)throw Error('Invalid merchant pagination');
  if(input.groupGrid!==undefined&&input.groupRegion!==undefined)throw Error('Only one geographic filter is allowed');
  const filterId=input.groupGrid??input.groupRegion;if(filterId!==undefined&&(!Number.isSafeInteger(filterId)||filterId<0))throw Error('Invalid geographic filter');
  if(matches.rows.length!==matches.tiers.length)throw Error('Match column lengths differ');
  const gridCounts=new Uint32Array(this.gridLabels.length),regionCounts=new Uint32Array(this.regionLabels.length),gridSeen=new Uint32Array(this.gridLabels.length),regionSeen=new Uint32Array(this.regionLabels.length),selected=new Uint8Array(matches.rows.length),tierCounts=new Uint32Array(9);
  let total=0,unassignedTotal=0,previous=-1;
  for(let i=0;i<matches.rows.length;i++){
   const row=matches.rows[i],tier=matches.tiers[i];if(row>=this.merchantCount||row<=previous||tier>8)throw Error('Matches must be unique ascending corpus rows with valid tiers');previous=row;
   const start=this.offsets[row],end=this.offsets[row+1],stamp=i+1;let allowed=filterId===undefined||(filterId===0&&start===end);
   if(start===end)unassignedTotal++;
   for(let link=start;link<end;link++){
    const grid=this.grids[link],region=this.regions[link];
    if(gridSeen[grid]!==stamp){gridSeen[grid]=stamp;gridCounts[grid]++;}if(regionSeen[region]!==stamp){regionSeen[region]=stamp;regionCounts[region]++;}
    if(input.groupGrid!==undefined&&this.gridLabels[grid].id===input.groupGrid||input.groupRegion!==undefined&&this.regionLabels[region].id===input.groupRegion)allowed=true;
   }
   if(allowed){selected[i]=1;total++;tierCounts[tier]++;}
  }
  const offset=(page-1)*pageSize,count=Math.max(0,Math.min(pageSize,total-offset)),rows=new Uint32Array(count),tiers=new Uint8Array(count);let skipped=0,filled=0;
  for(let tier=0;tier<9&&filled<count;tier++){
   if(skipped+tierCounts[tier]<=offset){skipped+=tierCounts[tier];continue;}
   for(let i=0;i<matches.rows.length&&filled<count;i++)if(selected[i]&&matches.tiers[i]===tier){if(skipped++<offset)continue;rows[filled]=matches.rows[i];tiers[filled]=tier;filled++;}
  }
  const groups=(labels:readonly MerchantGeographicGroup[],counts:Uint32Array)=>labels.flatMap((label,i)=>counts[i]?[{...label,count:counts[i]}]:[]).sort((a,b)=>a.id-b.id);
  return {total,matchedTotal:matches.rows.length,unassignedTotal,rows,tiers,page,pageSize,gridGroups:groups(this.gridLabels,gridCounts),regionGroups:groups(this.regionLabels,regionCounts)};
 }
}
