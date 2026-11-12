import {spawnLegacyPython} from './legacy-python.mjs';
/** Read-only, reproducible geometry acceptance. Python is an oracle only, never a runtime dependency. */
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {mergeGrids,gridToMetric,gridToGeographic,gridCoverageDifference,type GridGeometry,type GridPolygon} from '../packages/domain/src/grid-geometry';
const oracle=spawnLegacyPython(['legacy/scripts/grid-merge-oracle.py'],{encoding:'utf8',maxBuffer:4*1024*1024});
assert.equal(oracle.status,0,oracle.stderr);
const expected=JSON.parse(oracle.stdout.replace(/Infinity/g,'1e999'));
let maxProjectionErrorM=0;
for(const sample of expected.projection){const result=gridToMetric({type:'Polygon',coordinates:[[[sample.lon,sample.lat],[sample.lon+.001,sample.lat],[sample.lon+.001,sample.lat+.001],[sample.lon,sample.lat]]]}) as GridPolygon;const point=result.coordinates[0][0];maxProjectionErrorM=Math.max(maxProjectionErrorM,Math.hypot(point[0]-sample.point[0],point[1]-sample.point[1]));}
assert.ok(maxProjectionErrorM<.00001);
const cases=[];
for(const fixture of expected.cases){
 if(!fixture.allowed){await assert.rejects(()=>mergeGrids(fixture.grids,fixture.rivers.map(gridToGeographic)));cases.push({name:fixture.name,allowed:false,agrees:true});continue;}
 const result=await mergeGrids(fixture.grids,fixture.rivers.map(gridToGeographic)),areaErrorM2=Math.abs(result.areaM2-fixture.area),coverageErrorM2=await gridCoverageDifference(result.metric,fixture.metric),canonicalHash=createHash('sha256').update(Buffer.from(result.normalizedWkbHex,'hex')).digest('hex');
 assert.ok(areaErrorM2<=.01);assert.ok(coverageErrorM2<=.01);assert.equal(result.shape.acceptable,fixture.acceptable);assert.equal(canonicalHash,fixture.wkb_sha256);
 cases.push({name:fixture.name,allowed:true,agrees:true,areaM2:result.areaM2,areaErrorM2,coverageErrorM2,shapeAcceptable:result.shape.acceptable,canonicalHash,legacyHash:fixture.legacy_wkb_sha256});
}
const result={ok:true,readOnly:true,databaseAccess:false,projection:'EPSG:4326 -> EPSG:6677',precisionM:.001,areaToleranceM2:.01,maxProjectionErrorM,hashConvention:'little-endian normalized 2D WKB; signed zero canonicalized',cases};
await mkdir('data/refactor',{recursive:true});await writeFile('data/refactor/p5-grid-merge-geometry.json',JSON.stringify(result,null,2));console.log(JSON.stringify({ok:true,cases:cases.length,maxProjectionErrorM,maxAreaErrorM2:Math.max(...cases.map(c=>'areaErrorM2' in c?c.areaErrorM2:0)),maxCoverageErrorM2:Math.max(...cases.map(c=>'coverageErrorM2' in c?c.coverageErrorM2:0)),evidence:'data/refactor/p5-grid-merge-geometry.json'}));
