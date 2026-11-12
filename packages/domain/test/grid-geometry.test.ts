import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {mergeGrids,mergeMetricGrids,gridToMetric,gridToGeographic,gridCoverageDifference,type GridGeometry,type GridPolygon} from '../src/grid-geometry';
const square:GridPolygon={type:'Polygon',coordinates:[[[0,0],[1000,0],[1000,1000],[0,1000],[0,0]]]};
test('grid merging matches legacy EPSG:6677 precision, topology, holes, shape gates and river evidence',async()=>{
 const oracle=spawnLegacyPython(['legacy/scripts/grid-merge-oracle.py'],{encoding:'utf8',maxBuffer:4*1024*1024,env:{...process.env,PYTHONPATH:process.cwd()}});assert.equal(oracle.status,0,oracle.stderr);
 // Python emits Infinity for the legacy MultiPolygon shape metric; JSON fixture encodes it explicitly.
 const expected=JSON.parse(oracle.stdout.replace(/Infinity/g,'1e999'));
 for(const sample of expected.projection){const result=gridToMetric({type:'Polygon',coordinates:[[[sample.lon,sample.lat],[sample.lon+.001,sample.lat],[sample.lon+.001,sample.lat+.001],[sample.lon,sample.lat]]]});assert.ok(Math.hypot(result.coordinates[0][0][0]-sample.point[0],result.coordinates[0][0][1]-sample.point[1])<.00001);}
 for(const fixture of expected.cases){const run=()=>mergeGrids(fixture.grids,fixture.rivers.map(gridToGeographic));
  if(!fixture.allowed){await assert.rejects(run,undefined,fixture.name);continue;}
  const result=await run();assert.equal(createHash('sha256').update(Buffer.from(result.normalizedWkbHex,'hex')).digest('hex'),fixture.wkb_sha256,fixture.name+' canonical audit hash');assert.ok(Math.abs(result.areaM2-fixture.area)<=.01,fixture.name+' area');assert.ok(await gridCoverageDifference(result.metric,fixture.metric)<=.01,fixture.name+' coverage');assert.equal(result.shape.acceptable,fixture.acceptable,fixture.name+' shape');
  assert.ok(Math.abs(result.shape.solidity-fixture.solidity)<1e-8,fixture.name+' solidity');assert.equal(Number.isFinite(result.shape.aspect),Number.isFinite(fixture.aspect));if(Number.isFinite(fixture.aspect))assert.ok(Math.abs(result.shape.aspect-fixture.aspect)<1e-8,fixture.name+' aspect');assert.ok(Math.abs(result.shape.compactness-fixture.compactness)<1e-8,fixture.name+' compactness');
  assert.equal((await mergeMetricGrids([result.metric],fixture.rivers)).areaM2,result.areaM2,fixture.name+' stable');
  const reversed=await mergeGrids([...fixture.grids].reverse(),fixture.rivers.map(gridToGeographic));assert.equal(reversed.normalizedWkbHex,result.normalizedWkbHex,fixture.name+' input-order stability');
 }
});
test('grid geometry rejects malformed, empty, nonfinite and out-of-range inputs and remains stable across repeated errors',async()=>{
 for(const value of [null,{},[],{type:'Polygon',coordinates:[]},{type:'Polygon',coordinates:[[[0,0],[1,0],[1,1],[0,1]]]},{type:'Polygon',coordinates:[[[0,0],[1,0],[NaN,1],[0,0]]]}])await assert.rejects(()=>mergeMetricGrids([value as GridGeometry]));
 await assert.rejects(()=>mergeGrids([square])); // Metric coordinates are not geographic coordinates.
 await assert.rejects(()=>mergeMetricGrids([]));
 await assert.rejects(()=>mergeMetricGrids(Array(5001).fill(square)));
 const expected=await mergeMetricGrids([square]);
 for(let i=0;i<150;i++){await assert.rejects(()=>mergeMetricGrids([square,square]),{code:'GRID_OVERLAP'});const current=await mergeMetricGrids([square]);assert.equal(current.areaM2,expected.areaM2);}
});
