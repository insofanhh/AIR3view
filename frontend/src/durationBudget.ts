type Settings = Record<string, any>;

// Same arithmetic as backend.story.duration_budget_stats. Keep only target
// seconds and ratio in saved settings; never persist a second minimum value.
export function durationBudget(s: Settings, sourceSeconds?: number) {
  const count = s.output_mode === 'parts' ? (s.part_count ?? 3) : 1;
  const source = Number.isFinite(sourceSeconds) && Number(sourceSeconds) > 0 ? Number(sourceSeconds) : null;
  const automatic = s.output_mode === 'single' && s.summary_seconds === 0;
  const target = s.output_mode === 'parts' ? (s.part_seconds ?? 60) : automatic ? source : (s.summary_seconds ?? 180);
  const legacy = (s.production_workflow ?? 'legacy') === 'legacy';
  const ratio = legacy ? .75 : (s.duration_min_ratio ?? .9);
  const basis = target === null ? null : Math.min(target, source === null ? target : source / count);
  const minimum = basis === null ? null : Math.max(10, basis * ratio);
  return {count, source, target, ratio, basis, minimum, legacy, automatic,
    limitedBySource: basis !== null && target !== null && basis < target,
    provisional: source === null,
    impossible: target !== null && (target < 10 || (source !== null && source + 7 < count * 10)),
    minimumEditable: !legacy && basis !== null && basis >= 10};
}

export function ratioFromMinutes(s: Settings, source: number | undefined, minutes: number) {
  const b = durationBudget(s, source);
  if (!b.minimumEditable || b.basis === null || !Number.isFinite(minutes)) return b.ratio;
  return Math.min(1, Math.max(.6, minutes * 60 / b.basis));
}

export function budgetTime(seconds: number | null) {
  if (seconds === null) return 'chờ nguồn';
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60).toString().padStart(2, '0')}:${(rounded % 60).toString().padStart(2, '0')}`;
}

export function budgetSummary(s: Settings, source?: number) {
  const b = durationBudget(s, source);
  if (!s.output_mode) return 'Chọn cách xuất để tính thời lượng.';
  if (b.target === null) return 'Đang chờ thời lượng video gốc để tính khoảng đầu ra.';
  return b.count === 1
    ? `1 video · ${budgetTime(b.minimum)}–${budgetTime(b.target)}`
    : `${b.count} phần · ${budgetTime(b.minimum)}–${budgetTime(b.target)}/phần · tổng ${budgetTime(b.minimum! * b.count)}–${budgetTime(b.target * b.count)}`;
}
