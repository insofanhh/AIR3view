import { readFileSync } from 'node:fs';
import { transform } from 'esbuild';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const source=readFileSync(new URL('./src/autoRetry.ts',import.meta.url),'utf8');
const compiled=await transform(source,{loader:'ts',format:'esm'});
const {canAutoRetryDuration,isRetryableDurationFailure,MAX_DURATION_AUTO_RETRIES}=await import('data:text/javascript;base64,'+Buffer.from(compiled.code).toString('base64'));
const error='story-sel12 (12.96s → 14.53s): AI sửa thời lượng không hợp lệ sau giới hạn retry: AI không thay đổi lời kể để điều chỉnh thời lượng. Đã giữ nguyên lời kể và các đoạn hoàn tất; thử lại chỉ tiếp tục đoạn này.';

test('only saved duration-repair failures are retried',()=>{
  assert.equal(isRetryableDurationFailure({state:'failed',error}),true);
  assert.equal(isRetryableDurationFailure({state:'cancelled',error}),false);
  assert.equal(isRetryableDurationFailure({state:'interrupted',error}),false);
  assert.equal(isRetryableDurationFailure({state:'failed',error:'OpenAI 429: rate_limit_exceeded'}),false);
  assert.equal(isRetryableDurationFailure({state:'failed',error:'AI sửa thời lượng không hợp lệ sau giới hạn retry'}),false);
});

test('automatic retries stop after three attempts',()=>{
  const job={state:'failed',error};
  for(let attempt=0;attempt<MAX_DURATION_AUTO_RETRIES;attempt++)assert.equal(canAutoRetryDuration(job,attempt),true);
  assert.equal(canAutoRetryDuration(job,MAX_DURATION_AUTO_RETRIES),false);
});
