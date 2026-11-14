import test from 'node:test';import assert from 'node:assert/strict';
import {evaluateFuzzyNames,fuzzyEvaluationTarget,fuzzyNameEligible} from '../../../scripts/search-fuzzy-evaluation.mts';
const url=process.env.NEXT_TEST_DATABASE_URL,dataset=process.env.NEXT_SEARCH_FUZZY_DATASET;
test('fuzzy experiment can never target a business, production or non-disposable database',()=>{
 for(const target of ['postgresql://u:p@127.0.0.1/store_settle','postgresql://u:p@production/store_test_1234567890123_aabbccddeeff_refactor_p1','postgresql://u:p@127.0.0.1/store_settle_refactor_p1','mysql://u:p@127.0.0.1/store_test_1234567890123_aabbccddeeff_refactor_p1'])assert.throws(()=>fuzzyEvaluationTarget(target,'1'));
 assert.throws(()=>fuzzyEvaluationTarget('postgresql://u:p@127.0.0.1/store_test_1234567890123_aabbccddeeff_refactor_p1','0'));
});
test('fuzzy experiment excludes short, numeric, multiple-word and punctuated input without inferring identity',()=>{
 for(const q of ['新宿薬局','abc','123456','薬局 001','新宿 薬局','abc-def','ABC%_','a'.repeat(65),''])assert.equal(fuzzyNameEligible(q),false,q);
 for(const q of ['新宿中央薬局','chocoZAP大久保','ｃｈｏｃｏＺＡＰ大久保','ABCDE'])assert.equal(fuzzyNameEligible(q),true,q);
});
test('compare scoped trigram and one-edit candidates on frozen local tasks',{skip:!url||!dataset},async()=>{
 const {output,report}=await evaluateFuzzyNames(url!,dataset!);
 assert.equal(report.queries,120);assert.equal(report.extensionsInstalledOnlyInDisposableDatabase,true);assert.equal(report.productionAccessed,false);assert.equal(report.protectionCounterexample.distinct_merchant_edit_distance,1);
 for(const r of report.summary.filter(r=>r.kind==='original'))assert.equal(r.recallAt10,1,'exact literal results must never be replaced');
 console.log(JSON.stringify({fuzzyEvaluation:{output,...report}}));
});
