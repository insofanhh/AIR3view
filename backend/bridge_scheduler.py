"""Allocate real dialogue across every scene before filling the AI budget."""
from bisect import bisect_left,bisect_right

TICKS=30
MIN_AI=3*TICKS
MAX_AI=16*TICKS


def local_options(item,candidates):
    """A DAG of source intervals with bounded AI gaps; never invent source audio."""
    start,end=round(item['start']*30),round(item['end']*30)
    rows=sorted(set(c for c in candidates if start<=c[1]<c[2]<=end),key=lambda c:(c[1],c[2]))
    starts=[r[1] for r in rows]
    # Endpoint -> quantized retained time -> (actual ticks, score, windows, head).
    states={start:{0:(0,0.0,(),None)}}
    results={}
    for cursor in sorted({start}|{c[2] for c in rows}):
        options=states.get(cursor,{})
        if not options:continue
        remaining=end-cursor
        if remaining==0 or MIN_AI<=remaining<=MAX_AI:
            for used,score,chosen,head in options.values():
                head=(end-start) if head is None else head
                tail=remaining if chosen else end-start
                key=(round(used/15),head,tail)
                value=(used,score,chosen,head,tail)
                if key not in results or score>results[key][1]:results[key]=value
        left,right=bisect_left(starts,cursor),bisect_right(starts,cursor+MAX_AI)
        for priority,a,b,text in rows[left:right]:
            gap=a-cursor
            if gap and gap<MIN_AI:continue
            # Keep a short bridge at both structural edges when possible;
            # otherwise a whole selection becomes original-only and the story
            # loses its opening/ending context.
            if a==start and b<end and end-b<MIN_AI:continue
            if b==end and a>start and a-start<MIN_AI:continue
            target=states.setdefault(b,{})
            for used,score,chosen,head in list(options.values()):
                # Adjacent original intervals are allowed; cap their combined
                # run so a plan cannot hide a long monologue behind many cuts.
                run=b-a;edge=a
                for x in reversed(chosen):
                    if x[1]!=edge:break
                    run+=x[1]-x[0];edge=x[0]
                if run>90*TICKS:continue
                total=used+b-a
                first=a-start if head is None else head
                value=(total,score+priority*(b-a)-15,chosen+((a,b,text),),first)
                key=(round(total/15),first)
                if key not in target or value[1]>target[key][1]:target[key]=value
            # Bounded state count for unusually dense rolling transcripts.
            if len(target)>512:
                keep=sorted(target,key=lambda k:(target[k][0],target[k][1]),reverse=True)[:512]
                states[b]={k:target[k] for k in keep}
    return list(results.values())


def choose_windows(selections,candidates,maximum):
    """Meet the global budget without spending it all on the first long scenes."""
    states={(0,0):(0,0.0,(),0)}
    for index,item in enumerate(selections):
        local=local_options(item,candidates)
        if not local:
            raise ValueError(f'STORY_STRUCTURE: Cảnh {index+1} ({item["start"]:.2f}–{item["end"]:.2f}s) '
                             'chưa có cách xen thoại thật với bridge 3–8s; cần sửa riêng cảnh này.')
        next_states={}
        for used,score,chosen,tail in states.values():
            for amount,quality,windows,head,last in local:
                total=used+amount
                if total>maximum or tail+head>MAX_AI:continue
                value=(total,score+quality,chosen+windows,last)
                key=(round(total/15),last)
                if key not in next_states or value[1]>next_states[key][1]:next_states[key]=value
        if not next_states:
            raise ValueError('STORY_STRUCTURE: Ngân sách thoại gốc chưa đủ cho các cảnh đã chọn và các bridge xen kẽ. Cần chọn lại cảnh.')
        states=next_states
    chosen=sorted(max(states.values(),key=lambda v:(v[0],v[1]))[2])
    # Join touching original utterances so the final edit is a conversation,
    # not dozens of identical adjacent input streams with separate cuts.
    merged=[]
    boundaries={round(row['end']*30) for row in selections}
    for a,b,text in chosen:
        if merged and a==merged[-1][1] and a not in boundaries:
            merged[-1]=(merged[-1][0],b,merged[-1][2]+' '+text)
        else:merged.append((a,b,text))
    return merged


def trim_unavoidable_gaps(plan,project):
    """Shorten only long gaps without real speech, preserving source edges and minimum duration."""
    import copy
    from .retention import runs
    from .story import duration_budget_stats
    result=copy.deepcopy(plan);stats=duration_budget_stats(result,project)
    slack={part:max(0,int((seconds-stats['minimum'])*30)) for part,seconds in stats['totals'].items()}
    real=runs(project);output=[];changes=[]
    for row in result['selections']:
        a,b=round(row['start']*30),round(row['end']*30);cursor=a;gaps=[]
        for c in real:
            left,right=max(a,round(c['start']*30)),min(b,round(c['end']*30))
            if right<=left:continue
            if left>cursor:gaps.append((cursor,left))
            cursor=max(cursor,right)
        if cursor<b:gaps.append((cursor,b))
        cuts=[]
        for left,right in gaps:
            if right-left<=MAX_AI:continue
            amount=min(right-left-8*TICKS,slack[row['part']])
            if amount<right-left-MAX_AI:continue
            cut_start=left+4*TICKS;cut_end=cut_start+amount
            cuts.append((cut_start,cut_end));slack[row['part']]-=amount
            changes.append(dict(source_start=cut_start/30,source_end=cut_end/30,removed_seconds=amount/30))
        cursor=a
        for left,right in cuts:
            if left>cursor:output.append({**row,'start':cursor/30,'end':left/30,'narration':'Plan pending.','narration_offset':0})
            cursor=right
        if b>cursor:output.append({**row,'start':cursor/30,'end':b/30})
    if changes:
        for row in output:row['section']='development'
        output[0]['section']='opening';output[-1]['section']='ending'
        result['selections']=output
    return result,changes
