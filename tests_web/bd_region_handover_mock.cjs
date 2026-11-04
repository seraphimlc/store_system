const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
function harness({current='',selected='',answer=false}) {
  const handlers={};
  const button={textContent:current?'更新团队':'分配团队',dataset:{},classList:{toggle(){} }};
  const select={value:selected,selectedOptions:[{textContent:selected==='1'?'旧团队':'新团队'}],handlers:{},
    addEventListener(name,fn){this.handlers[name]=fn;},focus(){this.focused=true;}};
  const form={dataset:{currentTeam:current,currentTeamName:current?'旧团队':'',transferLabel:'确认交接',
    confirmMessage:'更换「{old}」到「{new}」会清空该片区网格的队员分配。'},handlers:{},
    querySelector(selector){return selector==='select[name="team_id"]'?select:button;},
    addEventListener(name,fn){this.handlers[name]=fn;}};
  const disclosure={dataset:{regionToggle:''},handlers:{},attributes:{'aria-expanded':'false','aria-controls':'region-1'},
    addEventListener(name,fn){this.handlers[name]=fn;},getAttribute(key){return this.attributes[key];},
    setAttribute(key,value){this.attributes[key]=value;}};
  const panel={hidden:true};
  const document={querySelectorAll(selector){return selector==='.batch-region-handover'?[form]:[disclosure];},
    getElementById(){return panel;}};
  const confirms=[];
  vm.runInNewContext(fs.readFileSync('app/static/bd_region_handover.js','utf8'),
    {document,window:{confirm(message){confirms.push(message);return answer;}}});
  return {form,select,button,disclosure,panel,confirms};
}
let h=harness({selected:'2'});
assert.equal(h.button.textContent,'分配团队');
let submitted=false;h.form.handlers.submit({preventDefault(){submitted=true;}});
assert.equal(submitted,false);assert.equal(h.confirms.length,0,'first assignment should not interrupt');

h=harness({current:'1',selected:'1'});submitted=false;
h.form.handlers.submit({preventDefault(){submitted=true;}});
assert.equal(submitted,false);assert.equal(h.confirms.length,0,'unchanged team should not interrupt');

h=harness({current:'1',selected:'2',answer:false});
assert.equal(h.button.textContent,'确认交接');
submitted=false;h.form.handlers.submit({preventDefault(){submitted=true;}});
assert.equal(submitted,true);assert.equal(h.select.focused,true);
assert.match(h.confirms[0],/旧团队.*新团队/);
assert.match(h.confirms[0],/清空该片区网格的队员分配/);

h=harness({current:'1',selected:'2',answer:true});submitted=false;
h.form.handlers.submit({preventDefault(){submitted=true;}});
assert.equal(submitted,false,'accepted handover proceeds normally');assert.equal(h.confirms.length,1);
h.disclosure.handlers.click();assert.equal(h.panel.hidden,false);
h.disclosure.handlers.click();assert.equal(h.panel.hidden,true);
console.log('region handover: confirm only changed assignments; cancel/accept and inline disclosure pass');
