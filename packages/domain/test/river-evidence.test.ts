import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {osmRiverFaces,gridToGeographic,gridCoverageDifference,type GridPolygon,type GridGeometry} from '../src/grid-geometry';
function ring(x:number,y:number,size:number){return (gridToGeographic({type:'Polygon',coordinates:[[[x,y],[x+size,y],[x+size,y+size],[x,y+size],[x,y]]]} as GridPolygon) as GridPolygon).coordinates[0].map(([lon,lat])=>({lon,lat}));}
test('tagged OSM river faces, fragmented relations, holes, duplicate identities and exclusion tags match legacy',async()=>{
 const outer=ring(0,0,1000),inner=ring(200,200,100);
 const elements=[{type:'way',id:1,tags:{natural:'water',water:'river'},geometry:ring(3000,0,500)},
  {type:'way',id:1,tags:{natural:'water',water:'river'},geometry:ring(3000,0,100)},
  {type:'way',id:2,tags:{natural:'water',water:'lake'},geometry:ring(5000,0,1000)},
  {type:'way',id:3,tags:{landuse:'forest'},geometry:ring(6000,0,1000)},
  {type:'way',id:4,tags:{water:'river'},geometry:ring(7000,0,1000)}, // Old exclusions requires a water-body tag too.
  {type:'way',id:5,tags:{waterway:'riverbank'},geometry:ring(8000,0,100)},
  {type:'way',id:6,tags:{waterway:'riverbank'},geometry:ring(9000,0,100).slice(0,3)},
  {type:'relation',id:7,tags:{natural:'water',water:'river'},members:[{type:'way',role:'outer',geometry:outer.slice(0,3)},{type:'way',role:'outer',geometry:[...outer.slice(2)]},{type:'way',role:'inner',geometry:inner}]}];
 const legacy=spawnLegacyPython(['legacy/scripts/river-evidence-oracle.py'],{encoding:'utf8',input:JSON.stringify(elements)});assert.equal(legacy.status,0,legacy.stderr);const expected=JSON.parse(legacy.stdout);
 const faces=await osmRiverFaces(elements);const combined:GridGeometry={type:'MultiPolygon',coordinates:faces.flatMap(face=>face.type==='Polygon'?[face.coordinates]:face.coordinates)};
 assert.equal(await gridCoverageDifference(combined,expected.geometry),0);assert.equal(expected.area,1010000);
 assert.deepEqual(await osmRiverFaces([]),[]);
});
