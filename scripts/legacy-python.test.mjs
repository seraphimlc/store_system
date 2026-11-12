import test from 'node:test';import assert from 'node:assert/strict';import{spawnLegacyPython}from'./legacy-python.mjs';
test('legacy Python adapter resolves archived packages while preserving caller environment and working directory',()=>{
 const result=spawnLegacyPython(['-c',"import os,json,app,store_settle;print(json.dumps({'app':app.__file__,'engine':store_settle.__file__,'cwd':os.getcwd(),'marker':os.environ['LEGACY_ADAPTER_TEST']}))"],{encoding:'utf8',env:{...process.env,LEGACY_ADAPTER_TEST:'preserved',PYTHONPATH:process.cwd()}});
 assert.equal(result.status,0,result.stderr);const value=JSON.parse(result.stdout);assert.match(value.app,/\/legacy\/app\/__init__\.py$/);assert.match(value.engine,/\/legacy\/store_settle\/__init__\.py$/);assert.equal(value.cwd,process.cwd());assert.equal(value.marker,'preserved');
});
