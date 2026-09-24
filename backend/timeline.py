import math
from .providers import voice_hash

FPS = 30


def frame(t):
    return round(t * FPS) / FPS


def shifted_words(cue, offset):
    return [{'text': w['text'], 'start': w['start'] + offset, 'end': w['end'] + offset} for w in cue.get('words', [])]


def build_legacy(project, strict=False):
    settings = project['settings']
    if strict and any(project.get(field) and project[field] != settings['language'] for field in ('script_language', 'title_language', 'transcript_language')):
        raise ValueError('Ngôn ngữ nội dung chưa khớp đầu ra. Chọn Chuyển text & tạo lại giọng trước khi xuất.')
    duration = project.get('metadata', {}).get('duration', 0)
    if not duration:
        return {'clips': [], 'voices': [], 'cues': [], 'parts': [], 'duration': 0, 'warnings': []}
    duration = math.ceil(duration * FPS) / FPS
    clips, voices, cues, warnings = [], [], [], list(project.get('warnings', []))
    cursor = 0.0

    def clip(kind, a, b, length=None):
        nonlocal cursor
        length = frame(length if length is not None else b - a)
        if length <= 0:
            return
        clips.append({'kind': kind, 'source_start': a, 'source_end': b, 'start': frame(cursor), 'end': frame(cursor + length)})
        cursor = frame(cursor + length)

    if settings['hook_enabled']:
        a, b = frame(settings['hook_start']), frame(min(settings['hook_end'], duration))
        if not 0 <= a < b <= duration:
            raise ValueError('Hook phải nằm trong video và mốc kết thúc phải lớn hơn bắt đầu.')
        clip('hook', a, b)
    hook_duration = cursor
    narrations = sorted((n for n in project['narrations'] if n['enabled'] and n['text'].strip()), key=lambda n: n['start'])
    source_cursor = 0
    previous_end = 0
    for narration in narrations:
        start = frame(narration['start'])
        if start >= duration:
            raise ValueError(f"Lời dẫn {narration['id']} bắt đầu ngoài video.")
        fresh = narration.get('audio_hash') == voice_hash(narration, settings) and narration.get('audio') and narration.get('duration', 0) > 0
        if not fresh:
            warnings.append(f"Lời dẫn {narration['id']} cần tạo giọng trước khi xuất.")
            if strict:
                raise ValueError(warnings[-1])
            continue
        voice_duration = narration['duration']
        if settings['narration_mode'] == 'insert':
            clip('original', source_cursor, start)
            voice_start = cursor
            clip('freeze', start, start, math.ceil(voice_duration * FPS) / FPS)
            source_cursor = start
        else:
            voice_start = hook_duration + start
            if start < previous_end - .001 or start + voice_duration > duration + .001:
                message = f"Lời dẫn {narration['id']} chồng câu khác hoặc vượt cuối video. Dời mốc hoặc rút ngắn lời để giữ hình chạy liên tục."
                warnings.append(message)
                if strict:
                    raise ValueError(message)
            previous_end = max(previous_end, start + voice_duration)
        voice = {'id': narration['id'], 'start': voice_start, 'end': voice_start + voice_duration, 'audio': narration['audio'], 'text': narration['text']}
        voices.append(voice)
        for cue in narration.get('cues', []):
            a, b = max(0, cue['start']), min(voice_duration, cue['end'])
            if b > a:
                cues.append({**cue, 'id': narration['id'] + '-' + cue['id'], 'start': voice_start + a, 'end': voice_start + b, 'speaker': 'ai', 'words': shifted_words(cue, voice_start)})
    clip('original', source_cursor, duration)
    for item in clips:
        if item['kind'] == 'freeze' or settings['original_volume'] == 0:
            continue
        for cue in project['transcript']:
            a = max(cue['start'], item['source_start'])
            b = min(cue['end'], item['source_end'])
            if b <= a:
                continue
            a, b = item['start'] + a - item['source_start'], item['start'] + b - item['source_start']
            ranges = [(a, b)]
            # Give narration captions priority, even when original audio is merely ducked.
            for voice in voices:
                next_ranges = []
                for left, right in ranges:
                    if voice['end'] <= left or voice['start'] >= right:
                        next_ranges.append((left, right))
                    else:
                        if left < voice['start']:
                            next_ranges.append((left, voice['start']))
                        if voice['end'] < right:
                            next_ranges.append((voice['end'], right))
                ranges = next_ranges
            for left, right in ranges:
                if right - left >= .08:
                    cues.append({**cue, 'start': left, 'end': right, 'speaker': 'original', 'words': shifted_words(cue, item['start'] - item['source_start'])})
    cues.sort(key=lambda c: (c['start'], c['end']))
    parts = split_parts(cursor, settings, cues, voices)
    for part in parts[:-1]:
        if any(c['start'] < part['end'] - .05 and c['end'] > part['end'] + .05 for c in cues):
            warnings.append(f"Ranh giới phần {part['index']} nằm giữa câu. Chọn chia tự nhiên hoặc sửa thời lượng.")
    return {'clips': clips, 'voices': voices, 'cues': cues, 'parts': parts, 'duration': cursor, 'warnings': list(dict.fromkeys(warnings))}


def split_parts(duration, settings, cues, voices):
    parts = []
    start = 0
    while start < duration - .01:
        index = len(parts)
        manual = index < len(settings['part_durations'])
        length = settings['part_durations'][index] if manual else settings['part_seconds']
        end = min(duration, frame(start + length))
        if not manual and settings['split_mode'] == 'natural' and end < duration:
            candidates = [frame(c['end']) for c in cues if abs(c['end'] - end) <= 5 and c['end'] > start + 1]
            safe = [t for t in candidates if not any(v['start'] + .05 < t < v['end'] - .05 for v in voices) and not any(c['start'] + .05 < t < c['end'] - .05 for c in cues)]
            if safe:
                end = min(duration, min(safe, key=lambda t: abs(t - end)))
        parts.append({'index': index + 1, 'start': start, 'end': end, 'duration': frame(end - start)})
        start = end
        if len(parts) > 5000:
            raise ValueError('Quá nhiều phần. Hãy tăng thời lượng mỗi phần.')
    return parts


def slice_clips(timeline, start, end):
    result = []
    for c in timeline['clips']:
        a, b = max(start, c['start']), min(end, c['end'])
        if b <= a:
            continue
        source_start = c['source_start'] if c['kind'] == 'freeze' else c['source_start'] + a - c['start']
        result.append({**c, 'start': a - start, 'end': b - start, 'source_start': source_start, 'source_end': source_start if c['kind'] == 'freeze' else source_start + b - a})
    return result


def build(project, strict=False):
    if not project['settings'].get('output_mode'):
        return build_legacy(project, strict)
    from .story import plan_is_current
    if not plan_is_current(project):
        message = 'Cấu hình đầu ra chưa có bản chọn cảnh phù hợp. Bấm Phân tích AI hoặc Chạy toàn bộ để lập lại kịch bản.'
        if strict:
            raise ValueError(message)
        # The source remains available to review before the first editorial plan.
        result = build_legacy(project, False)
        result['warnings'].insert(0,message)
        result['planned'] = False
        return result
    return build_story(project, strict)


def build_story(project, strict=False):
    import copy
    from .story import storytelling, validate_narration_budget
    settings = project['settings']
    if strict:
        from .plan_first import contract_check
        contract_check(project)
    plan = project['story_plan']
    narrated = storytelling(settings)
    if strict and narrated:
        # The actual WAV lengths are checked below. A newly created/shared
        # speaker must not retroactively change the script's word budget.
        validate_narration_budget(plan, project, check_text=False)
        from .hook_policy import slots
        expected = {x['id'] for x in slots(plan) if x['narration'].strip()}
        present = {n.get('segment_id') for n in project['narrations'] if n['enabled'] and n['text'].strip()}
        if not expected <= present:
            raise ValueError('Thiếu lời kể cho cảnh AI. Tạo lại kịch bản để giữ tỷ lệ lời kể và thoại gốc.')
    clips, parts, mapped_cues, mapped_narrations, warnings = [], [], [], [], []
    cursor = 0.0
    def append(kind, a, b, part, identifier='', section=''):
        nonlocal cursor
        a,b = frame(a),frame(b)
        c = dict(kind=kind,source_start=a,source_end=b,start=cursor,end=frame(cursor+b-a),
                 part=part,segment_id=identifier,section=section)
        clips.append(c)
        cursor=c['end']
    append('hook',settings['hook_start'],settings['hook_end'],1,'hook','hook')
    for item in plan['selections']:
        append('highlight',item['start'],item['end'],item['part'],item['id'],item['section'])
    for index in sorted(set(c['part'] for c in clips)):
        group=[c for c in clips if c['part']==index]
        parts.append(dict(index=index,start=group[0]['start'],end=group[-1]['end'],duration=frame(group[-1]['end']-group[0]['start'])))
    original_audio = []
    source_mutes = []
    from .source_speech import active, muted_ranges
    excluded = muted_ranges(project) if narrated and active(project) else []
    selections = {x['id']: x for x in plan['selections']}
    for clip in clips:
        for left,right in excluded:
            a,b = max(left,clip['source_start']),min(right,clip['source_end'])
            if b>a:
                offset=clip['start']-clip['source_start']
                source_mutes.append({'start':a+offset,'end':b+offset})
        if narrated and clip['kind']=='hook' and not plan['hook'].get('original_audio',True):
            source_mutes.append({'start':clip['start'],'end':clip['end']})
        if narrated:
            keep = plan['hook'].get('original_audio',True) if clip['kind']=='hook' else not selections[clip['segment_id']]['narration'].strip()
            if not keep:
                continue
            if project['metadata'].get('has_audio'):
                original_audio.append({'start': clip['start'], 'end': clip['end']})
        for cue in project['transcript']:
            a,b=max(cue['start'],clip['source_start']),min(cue['end'],clip['source_end'])
            if b>a:
                offset=clip['start']-clip['source_start']
                mapped_cues.append({**cue,'start':a+offset,'end':b+offset,'words':shifted_words(cue,offset)})
    for n in project['narrations']:
        if not n['enabled'] or not n['text'].strip(): continue
        candidates=[c for c in clips if (c['kind']!='hook' or n.get('segment_id')=='hook') and c['source_start']<=n['start']<c['source_end']]
        if n.get('segment_id'):
            candidates=[c for c in candidates if c['segment_id']==n['segment_id']]
        if not candidates:
            message=f"Lời dẫn {n['id']} nằm ngoài cảnh được chọn. Dời mốc vào cảnh hoặc phân tích lại."
            if strict: raise ValueError(message)
            warnings.append(message)
            continue
        clip=candidates[0]
        start=frame(clip['start']+n['start']-clip['source_start'])
        if n.get('duration',0)+start>clip['end']+.001:
            message=f"Lời dẫn {n['id']} dài hơn cảnh đã chọn. Rút ngắn lời hoặc phân tích lại trước khi xuất."
            if strict: raise ValueError(message)
            warnings.append(message)
        if not narrated and n.get('section')=='opening' and n['start']-clip['source_start']<settings.get('opening_delay',3)-.001:
            message='Lời mở đầu cần phát sau đoạn hình gốc, không ngay sau hook.'
            if strict: raise ValueError(message)
            warnings.append(message)
        mapped_narrations.append({**n,'start':start})
    virtual=copy.deepcopy(project)
    virtual.update(metadata={**project['metadata'],'duration':cursor},transcript=mapped_cues,narrations=mapped_narrations)
    virtual['settings'].update(hook_enabled=False,narration_mode='overlay')
    result=build_legacy(virtual,strict)
    # Clip and part boundaries belong to the editorial plan, not source splitting.
    result.update(clips=clips,parts=parts,duration=cursor,planned=True)
    if narrated:
        original_ratio = sum(x['end']-x['start'] for x in original_audio)/cursor
        ai_ratio = sum(v['end']-v['start'] for v in result['voices'])/cursor
        result.update(original_audio=original_audio, narration_mix={'original_ratio': original_ratio, 'ai_ratio': ai_ratio})
        result['source_mutes'] = source_mutes
        from .retention import budget as retention_budget
        result['retention']=retention_budget(plan,project)
        planned_ai_ratio = sum(c['end']-c['start'] for c in clips
                               if (bool(plan['hook'].get('narration')) if c['kind']=='hook' else bool(selections[c['segment_id']]['narration'].strip())))/cursor
        if strict and ai_ratio < planned_ai_ratio-.02:
            raise ValueError(f'Lời AI mới phủ {ai_ratio:.0%} video. Cần tạo đủ lời kể theo thời lượng cảnh trước khi xuất.')
    result['warnings']=list(dict.fromkeys(warnings+[w for w in result['warnings'] if not w.startswith('Ranh giới phần')]))
    for part in parts[:-1]:
        if any(v['start']<part['end']<v['end'] for v in result['voices']):
            raise ValueError('Lời AI vượt ranh giới phần; rút ngắn lời trước khi xuất.')
    return result
