"""Measured original-audio target, checked before prose/TTS, with source limits."""
VERSION=1
TOLERANCE=.03


def runs(project):
    """Union real speech, subtract all excluded speakers, never sum overlapping captions."""
    if not project['metadata'].get('has_audio',True):return []
    rows=sorted(project.get('source_speech',{}).get('items',[]),key=lambda c:c['start'])
    real=[c for c in rows if c['role']=='participant' and c['confidence']>=.7]
    excluded=[c for c in rows if c['role']!='participant' or c['confidence']<.7]
    groups=[]
    for c in real:
        if groups and c['start']<=groups[-1]['end']+.5:
            groups[-1]['end']=max(groups[-1]['end'],c['end'])
            groups[-1]['priority']=max(groups[-1]['priority'],c['priority'])
        else:groups.append(dict(start=c['start'],end=c['end'],priority=c['priority']))
    for bad in excluded:
        next_groups=[]
        for g in groups:
            if bad['end']<=g['start'] or bad['start']>=g['end']:next_groups.append(g);continue
            if bad['start']>g['start']:next_groups.append({**g,'end':bad['start']})
            if bad['end']<g['end']:next_groups.append({**g,'start':bad['end']})
        groups=next_groups
    from .source_speech import allowed
    return [g for g in groups if g['end']-g['start']>=1 and allowed(project,g['start'],g['end'])]


def candidates(project,start,end,min_gap=4,max_length=60):
    """Alternatives at cue boundaries, including prefixes of long conversations."""
    from .source_speech import allowed
    tick=lambda t:round(t*30)
    rows=project.get('source_speech',{}).get('items',[])
    points={tick(c[k]) for c in rows for k in ('start','end')}
    low,high=tick(start),tick(end)
    result=[]
    for g in runs(project):
        a,b=max(low,tick(g['start'])),min(high,tick(g['end']))
        if b-a<30:continue
        starts=sorted(({a} if a in points or a==tick(g['start']) else set())|{t for t in points if a<t<b})
        ends=sorted(({b} if b in points or b==tick(g['end']) else set())|{t for t in points if a<t<b})
        for left in starts:
            if 0<left-low<min_gap*30:continue
            for right in ends:
                if not 30<=right-left<=max_length*30 or 0<high-right<min_gap*30:continue
                if (project.get('transcript_origin')=='youtube_auto_subtitles'
                        or allowed(project,left/30,right/30)):
                    text=' '.join(c.get('text','') for c in rows if c['start']<right/30 and c['end']>left/30)
                    result.append((g['priority'],left,right,text))
    return result


def budget(plan,project):
    h=plan['hook'];hook=h['end']-h['start']
    total=hook+sum(c['end']-c['start'] for c in plan['selections'])
    hook_original=hook if h.get('original_audio',True) else 0
    requested=total*project['settings'].get('original_dialogue_ratio',.15)
    available=sum(g['end']-g['start'] for g in runs(project))+hook_original
    from .story_bridges import reserve_seconds
    structural_reserve=reserve_seconds(plan,project)
    feasible=min(requested,available,max(0,total-(hook-hook_original)-structural_reserve))
    actual=hook_original+sum(c['end']-c['start'] for c in plan['selections'] if not c['narration'].strip())
    return dict(requested_ratio=project['settings'].get('original_dialogue_ratio',.15),total_seconds=round(total,3),
                requested_seconds=round(requested,3),available_seconds=round(available,3),
                effective_seconds=round(feasible,3),actual_seconds=round(actual,3),
                actual_ratio=actual/total if total else 0,ai_planned_ratio=1-actual/total if total else 0,
                shortfall_seconds=round(max(0,requested-feasible),3),structural_reserve_seconds=structural_reserve,
                source_shortfall_seconds=round(max(0,requested-available),3),tolerance_seconds=max(1,total*TOLERANCE))


def validate(plan,project):
    if project.get('retention_policy_version')!=VERSION:return
    b=budget(plan,project)
    # 10–50% remains a ceiling for backward-compatible plans. Above 50% the
    # user explicitly asks the planner to replace AI narration with source
    # dialogue, so fail before TTS if the selected footage misses the target.
    if b['requested_ratio']<=.5:return
    if b['actual_seconds']+b['tolerance_seconds']<b['effective_seconds']:
        raise ValueError(f'ORIGINAL_AUDIO_TARGET: Thoại gốc mới đạt {b["actual_ratio"]:.1%} '
            f'({b["actual_seconds"]:.1f}s/{b["total_seconds"]:.1f}s); mục tiêu {b["requested_ratio"]:.0%}, '
            f'cần khoảng {b["effective_seconds"]:.1f}s tiếng thật, nguồn có {b["available_seconds"]:.1f}s khả dụng. '
            'Chọn lại cảnh từ các khoảng hội thoại thật, giảm cảnh lời bình; vẫn giữ diễn biến và kết quả. Chưa tạo voice AI.')


def instructions(project):
    from .story import duration_budget_stats
    from .hook_policy import source_hook
    import json
    stats=duration_budget_stats({},project);ratio=project['settings'].get('original_dialogue_ratio',.15)
    hook=source_hook(project)
    blocks=runs(project)
    available=sum(g['end']-g['start'] for g in blocks)
    # A concise prioritized reservation proposal guides the AI's story selection;
    # the model must still cover context/outcome and can choose other real spans.
    remaining=max(0,stats['target']*stats['count']*ratio-(hook['end']-hook['start'] if hook else 0))
    proposals=[]
    # Seed different stages of the source instead of spending every second
    # on a high-scoring late interview and losing the initiating conflict.
    ordered=sorted(blocks,key=lambda g:(g['priority'],g['end']-g['start']),reverse=True)
    seeds=[]
    for zone in range(3):
        seed=next((g for g in ordered if min(2,int(g['start']/max(1,stats['source'])*3))==zone),None)
        if seed:seeds.append(seed)
    ordered=seeds+[g for g in ordered if g not in seeds]
    for g in ordered:
        choices=candidates(project,g['start'],g['end'],min_gap=0,max_length=min(45,remaining)) if remaining>=1 else []
        if choices:
            _,a,b,_=max(choices,key=lambda c:c[2]-c[1])
            proposals.append(dict(start=a/30,end=b/30,priority=g['priority']));remaining-=(b-a)/30
        if len(proposals)>=20 or remaining<1:break
    if ratio<=.5:
        return ('\nOriginal dialogue remains a ceiling in narrator-led mode. Select genuine exchanges within the cap; '
                'do not force a minimum when unavailable.\nREAL SPEECH RANGES: '+json.dumps(blocks,ensure_ascii=False))
    return ('\nORIGINAL_AUDIO_TARGET CONTRACT v1 (overrides ceiling-only wording): '
        f'Target real source audio {ratio:.0%}, AI narration approximately {1-ratio:.0%} of output. '
        f'For {stats["target"]*stats["count"]:.1f}s output reserve about {stats["target"]*stats["count"]*ratio:.1f}s real source audio BEFORE choosing narrated footage. '
        f'Unique classified real speech available: {available:.1f}s (overlapping caption times counted once). '
        'Aim within 3 percentage points BELOW target without exceeding the cap. '
        'Only if the whole source truly lacks enough eligible speech may actual retention fall lower; never pad with commentary, silence or repeated footage. '
        'Preserve whole-story context, key escalation and actual outcome; narration should compactly explain what real dialogue cannot. '
        'Use explicit real-speech source ranges; leave narration gaps >=4s or no gap. '
        'YouTube rolling subtitle overlaps are display updates, not a requirement to keep every overlapping cue whole. '
        '\nREAL SPEECH RANGES: '+json.dumps(blocks,ensure_ascii=False)+
        '\nPRIORITY RESERVATION PROPOSAL (chronologically reorder, retain ending, adapt if needed): '+json.dumps(sorted(proposals,key=lambda c:c['start'])))
