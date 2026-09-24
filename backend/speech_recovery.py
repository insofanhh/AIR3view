"""Recover cue roles by ID, retaining valid work without accepting ambiguity."""
import json
from collections import defaultdict
from pydantic import ValidationError


def partition(answer,requested):
    grouped=defaultdict(list);extras=[]
    for row in answer.items:
        if row.cue in requested:grouped[row.cue].append(row)
        else:extras.append(row.cue)
    valid={key:values[0] for key,values in grouped.items() if len(values)==1}
    return valid,dict(missing=sorted(requested-set(grouped)),
                      duplicates=sorted(key for key,values in grouped.items() if len(values)>1),
                      extra=sorted(set(extras)))


def recover(batch,prompt,cached,path,ask_ai,settings,folder,report,check):
    from .source_speech import SpeechRole, SpeechRoles
    ids={c['cue'] for c in batch};accepted={};details={};generation=0
    state_dir=path.parent/'recovery';state_dir.mkdir(exist_ok=True)
    state_path=state_dir/path.name
    history=[]
    if state_path.is_file():
        try:
            state=json.loads(state_path.read_text('utf-8'))
            generation=max(0,int(state['generation']))
            answer=SpeechRoles.model_validate({'items':state['accepted']})
            accepted,_=partition(answer,ids)
            history=state.get('history',[])[-20:]
        except (OSError,ValueError,KeyError,TypeError):
            accepted={};generation=0;history=[]
    if cached is not None:
        recovered,details=partition(cached,ids)
        for key,row in recovered.items():accepted.setdefault(key,row)

    def save():
        temporary=state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(generation=generation,accepted=[r.model_dump() for r in accepted.values()],
            pending=sorted(ids-set(accepted)),history=history[-20:]),ensure_ascii=False),'utf-8')
        try:
            temporary.replace(state_path)
        except PermissionError:
            # Windows test runners/antivirus can hold a previous checkpoint;
            # retain recovery state under a generation-specific file instead
            # of turning a successful classification into a pipeline failure.
            fallback=state_path.with_name(state_path.stem+f'.g{generation}.json')
            temporary.replace(fallback)

    calls=0
    # Smaller requests after each round avoid asking for the same 80-item
    # envelope again. With 80 input IDs this is at most 15 calls per run.
    for round_index,size in enumerate((80,40,20,10)):
        check()
        pending=[c for c in batch if c['cue'] not in accepted]
        if not pending:break
        for offset in range(0,len(pending),size):
            check();subset=pending[offset:offset+size];requested={c['cue'] for c in subset}
            fresh = generation==0 and not accepted and len(subset)==len(batch)
            generation+=1;save()  # Advance BEFORE calling; retry won't replay bad provider cache.
            if fresh:
                request=prompt  # Recover the exact existing provider response on first use.
            else:
                context=prompt.split('\nREQUESTED CUES: ',1)[0]
                request=(context+f'\nREPAIR GENERATION {generation}; ROUND {round_index+1}/4. '
                    'CONTEXT cues are read-only evidence, NEVER IDs to return. '
                    'Return ONLY the requested unresolved IDs, each once. Accepted roles are immutable. '
                    '\nPREVIOUS ID ERRORS: '+json.dumps(details,ensure_ascii=False)+
                    '\nREQUIRED IDS: '+json.dumps(sorted(requested))+
                    '\nREQUESTED CUES: '+json.dumps(subset,ensure_ascii=False))
            report(85,f'Phân loại thoại · cue {min(ids)}–{max(ids)} · đã giữ {len(accepted)}/{len(ids)} · vòng {round_index+1}/4…')
            calls+=1
            try:
                answer=SpeechRoles.model_validate(ask_ai(request,[],settings,folder,check,SpeechRoles))
                valid,details=partition(answer,requested)
                accepted.update(valid)
            except ValidationError as exc:
                # Authentication, transport and cancellation errors propagate.
                details=dict(missing=sorted(requested),duplicates=[],extra=[],
                             schema=exc.errors(include_input=False,include_url=False)[:3])
            history.append(dict(generation=generation,round=round_index+1,requested=sorted(requested),
                                errors=details,accepted_count=len(accepted)))
            check();save()
    if set(accepted)!=ids:
        unresolved=sorted(ids-set(accepted))
        raise ValueError(f'Phân loại thoại nguồn còn thiếu/lặp ID ở lô {min(ids)}–{max(ids)}: '
            f'đã lưu {len(accepted)}/{len(ids)} cue hợp lệ, còn {unresolved[:20]} sau 4 vòng ({calls} lần gọi). '
            'Thử lại chỉ xử lý ID chưa xong bằng yêu cầu mới; không thay vai trò bằng phỏng đoán.')
    answer=SpeechRoles(items=[accepted[key] for key in sorted(ids)])
    # Validate the disk representation before publishing a completed batch.
    check();temporary=path.with_suffix('.tmp')
    temporary.write_text(answer.model_dump_json(),'utf-8')
    reread=SpeechRoles.model_validate_json(temporary.read_text('utf-8'))
    valid,_=partition(reread,ids)
    if set(valid)!=ids:raise ValueError('Kiểm tra cache phân loại không đạt; chưa công bố lô này.')
    check();temporary.replace(path)
    return answer
