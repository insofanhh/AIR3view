export const MAX_DURATION_AUTO_RETRIES = 3;

export function isRetryableDurationFailure(job: {state:string; error?:string} | null | undefined): boolean {
  if (job?.state !== 'failed') return false;
  const error = job.error || '';
  if (/invalid_json_schema|\[AI:authentication\]|\[AI:quota\]|\[visual:/i.test(error)) return false;
  return error.includes('AI sửa thời lượng không hợp lệ sau giới hạn retry')
    && error.includes('Đã giữ nguyên lời kể và các đoạn hoàn tất; thử lại chỉ tiếp tục đoạn này.');
}

export function canAutoRetryDuration(job: {state:string; error?:string} | null | undefined, attempts:number): boolean {
  return isRetryableDurationFailure(job) && attempts < MAX_DURATION_AUTO_RETRIES;
}
