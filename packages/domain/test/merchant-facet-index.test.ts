import test from 'node:test';import assert from 'node:assert/strict';import {MerchantFacetIndex} from '../src/merchant-facet-index';
const grids=[{id:1,name:'G1',regionId:10,regionName:'R1'},{id:2,name:'G2',regionId:10,regionName:'R1'},{id:3,name:'G3',regionId:20,regionName:'R2'}],regions=[{id:10,name:'R1'},{id:20,name:'R2'}];
const matches={rows:Uint32Array.from([0,1,2,3,4]),tiers:Uint8Array.from([3,1,0,1,8])};
const links={rows:Uint32Array.from([0,0,0,1,2]),gridIds:Uint32Array.from([1,2,3,1,2]),regionIds:Uint32Array.from([10,10,20,10,10])};
test('packed geography counts full unique merchant set, not task or relationship rows, before rank and pagination',()=>{
 const index=new MerchantFacetIndex(5,links,grids,regions),first=index.select(matches,{pageSize:2}),second=index.select(matches,{pageSize:2,page:2}),third=index.select(matches,{pageSize:2,page:3});
 assert.equal(first.total,5);assert.equal(first.matchedTotal,5);assert.equal(first.unassignedTotal,2);
 assert.deepEqual(first.gridGroups.map(g=>[g.id,g.count]),[[1,2],[2,2],[3,1]]);assert.deepEqual(first.regionGroups.map(g=>[g.id,g.count]),[[10,3],[20,1]]);
 assert.deepEqual(first.gridGroups,second.gridGroups);assert.deepEqual(Array.from(first.rows),[2,1]);assert.deepEqual(Array.from(second.rows),[3,0]);assert.deepEqual(Array.from(third.rows),[4]);
 assert.deepEqual(Array.from(index.select(matches,{groupGrid:2}).rows),[2,0]);assert.deepEqual(Array.from(index.select(matches,{groupRegion:20}).rows),[0]);assert.deepEqual(Array.from(index.select(matches,{groupGrid:0}).rows),[3,4]);assert.equal(index.select(matches,{groupGrid:999}).total,0);
 assert.equal(index.select(matches,{page:1000}).rows.length,0);assert.deepEqual(index.select({rows:new Uint32Array(0),tiers:new Uint8Array(0)}).gridGroups,[]);
});
test('duplicate relationships cannot inflate geographic facets; source input is not retained mutable',()=>{
 const input={rows:Uint32Array.from([0,0]),gridIds:Uint32Array.from([1,1]),regionIds:Uint32Array.from([10,10])},labels=grids.map(g=>({...g})),index=new MerchantFacetIndex(1,input,labels,regions);
 input.gridIds[0]=3;labels[0].name='changed';const result=index.select({rows:Uint32Array.of(0),tiers:Uint8Array.of(1)});assert.equal(result.gridGroups[0].name,'G1');assert.equal(result.gridGroups[0].count,1);assert.equal(result.regionGroups[0].count,1);
});
test('malformed identities, rank order, pagination and conflicting filters fail closed',()=>{
 assert.throws(()=>new MerchantFacetIndex(5,{...links,rows:Uint32Array.of(2,1)},grids,regions),/lengths/);
 assert.throws(()=>new MerchantFacetIndex(1,links,grids,regions),/valid/);assert.throws(()=>new MerchantFacetIndex(5,links,[...grids,grids[0]],regions),/unique/);
 assert.throws(()=>new MerchantFacetIndex(5,links,grids,[{id:10,name:'R1'}]),/Unknown/);
 const index=new MerchantFacetIndex(5,links,grids,regions);
 assert.throws(()=>index.select(matches,{groupGrid:0,groupRegion:0}),/one/);assert.throws(()=>index.select(matches,{page:0}),/pagination/);assert.throws(()=>index.select(matches,{groupGrid:-1}),/filter/);
 assert.throws(()=>index.select({rows:Uint32Array.of(0,0),tiers:Uint8Array.of(1,1)}),/unique/);assert.throws(()=>index.select({rows:Uint32Array.of(0),tiers:Uint8Array.of(9)}),/tiers/);
});
