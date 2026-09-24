import { readFileSync } from 'node:fs';
import { transform } from 'esbuild';
import { test } from 'node:test';
import assert from 'node:assert/strict';
const source=readFileSync(new URL('./src/durationBudget.ts',import.meta.url),'utf8');
const compiled=await transform(source,{loader:'ts',format:'esm'});
const {durationBudget,ratioFromMinutes,budgetSummary}=await import('data:text/javascript;base64,'+Buffer.from(compiled.code).toString('base64'));
const single={output_mode:'single',summary_seconds:300,production_workflow:'plan_first',duration_min_ratio:.9};
test('minutes and ratio stay in sync in both directions',()=>{
  assert.equal(durationBudget(single,1440).minimum,270);
  assert.equal(ratioFromMinutes(single,1440,4),.8);
  assert.equal(durationBudget({...single,duration_min_ratio:.8},1440).minimum,240);
  assert.equal(durationBudget({...single,summary_seconds:600},1440).minimum,540);
  assert.equal(budgetSummary(single,1440),'1 video · 04:30–05:00');
});
test('source-length mode, source limit, per-part budget and legacy match backend',()=>{
  assert.equal(durationBudget({...single,summary_seconds:0},600).minimum,540);
  assert.equal(durationBudget(single,120).minimum,108);
  const parts={...single,output_mode:'parts',part_count:3,part_seconds:300};
  assert.equal(durationBudget(parts,600).minimum,180);
  assert.equal(ratioFromMinutes(parts,600,2.5),.75);
  assert.equal(durationBudget({...single,production_workflow:'legacy'},1000).minimum,225);
});
test('empty source, impossible targets, inverse bounds and precise ratios',()=>{
  assert.equal(durationBudget({...single,summary_seconds:0}).minimum,null);
  assert.equal(durationBudget(single).minimum,270);
  assert.equal(durationBudget({...single,summary_seconds:5},100).impossible,true);
  assert.equal(ratioFromMinutes(single,600,1),.6);
  assert.equal(ratioFromMinutes(single,600,9),1);
  assert.ok(Math.abs(ratioFromMinutes(single,600,4.123)-4.123/5)<1e-12);
  const parts={...single,output_mode:'parts',part_count:4,part_seconds:30};
  assert.equal(durationBudget(parts,20).impossible,true);
  assert.equal(single.summary_seconds,300); // pure derived UI; no silent edits
});
