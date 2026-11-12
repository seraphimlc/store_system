import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {spawnSync} from 'node:child_process';
import {bonusConfig,salaryFor,halfGross,payPlan,allocateFifo,previousMonth} from '../src/payroll';
test('salary and rolling half bonuses match legacy schedules and latest global configuration',()=>{
 const scenarios=[{month:'2026-08',points:68},{month:'2026-09',points:75},{month:'2026-10',points:151},{month:'2026-08',points:0},{month:'2026-09',points:-3},{month:'2027-01',points:77,config:{group:70,amount:4000,perPoint:300}}];
 const source=`import json,sys\nfrom types import SimpleNamespace\nfrom app.services import perf\nx=json.load(sys.stdin)\nout=[]\nfor v in x:\n c=v.get('config')\n perf._CONFIG_CACHE['row']=SimpleNamespace(bonus_group=c['group'],bonus_amount=c['amount'],per_point=c['perPoint']) if c else None\n g,a=perf.bonus_params(v['month'])\n out.append({'config':[g,a,c['perPoint'] if c else 250],'salary':perf.salary_for(v['points'],c['perPoint'] if c else 250,v['month'])})\nprint(json.dumps(out))`;
 const legacy=spawnLegacyPython(['-c',source],{encoding:'utf8',input:JSON.stringify(scenarios),env:{...process.env,DATABASE_URL:'sqlite://'}});assert.equal(legacy.status,0);const expected=JSON.parse(legacy.stdout);
 scenarios.forEach((scenario,index)=>{const config=bonusConfig(scenario.month,{},scenario.config);assert.deepEqual([config.group,config.amount,config.perPoint],expected[index].config);assert.equal(salaryFor(scenario.points,config),expected[index].salary);});
 const halves=halfGross(50,30,bonusConfig('2026-09'));assert.equal(halves.h1.bonus,0);assert.equal(halves.h2.bonus,1250);assert.equal(halves.h1.amount+halves.h2.amount,salaryFor(80,bonusConfig('2026-09')));
 assert.equal(previousMonth('2027-01'),'2026-12');assert.throws(()=>bonusConfig('2026-13'));assert.throws(()=>salaryFor(Number.MAX_SAFE_INTEGER,bonusConfig('2026-09')));assert.throws(()=>halfGross(1.5,1,bonusConfig('2026-09')));
});
test('two-half pay plan retains recovered adjustment and carries insufficient deduction without negative wages',()=>{
 assert.deepEqual(payPlan(1000,2000,-1500),{h1:[-1500,0],h2:[-500,1500]});
 assert.deepEqual(payPlan(1000,2000,-1500,-800),{h1:[-1500,0],h2:[-700,1300]});
 assert.deepEqual(payPlan(1000,2000,500),{h1:[500,1500],h2:[0,2000]});
 assert.deepEqual(payPlan(0,0,-500),{h1:[-500,0],h2:[-500,0]});
 assert.deepEqual(payPlan(1000,2000,-1000,-1000),{h1:[-1000,0],h2:[0,2000]});
});
test('FIFO allocation consumes oldest same-sign debt, preserves opposite-sign balances and exposes unallocated remainder',()=>{
 const debts=[{id:3,sourceMonth:'2026-09',remaining:-300,settled:0},{id:2,sourceMonth:'2026-08',remaining:500,settled:0},{id:1,sourceMonth:'2026-07',remaining:-200,settled:-100}];
 const result=allocateFifo(debts,-400);assert.deepEqual(result.allocations,[{adjustId:1,sourceMonth:'2026-07',amount:-200,remaining:0,settled:-300},{adjustId:3,sourceMonth:'2026-09',amount:-200,remaining:-100,settled:-200}]);assert.equal(result.unallocated,0);assert.equal(debts[2].remaining,-200);
 assert.equal(allocateFifo(debts,-1000).unallocated,-500);assert.equal(allocateFifo(debts,700).unallocated,200);
});
