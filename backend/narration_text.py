"""Remove clear editing citations from narration, never from source evidence."""
import copy
import re

_NUMBER=r'\d+(?:[.,]\d+)?'
_CLOCK=r'\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?'
_PREFIX=re.compile(
    r'(?:\A|(?<=[.!?])\s+)'
    r'(?:at\s+(?:(?:the\s+)?(?:source|video|footage)\s+)?'+_NUMBER+r'\s*(?:seconds?|secs?)'
    r'|(?:at\s+)?(?:the\s+)?(?:source|video|footage)\s+(?:timestamp|timecode|time)\s*'+_CLOCK+
    r'|(?:tại|ở)\s+(?:mốc\s+)?giây\s*(?:thứ\s+)?'+_NUMBER+
    r'|(?:tại|ở)\s+mốc\s*(?:thời gian\s*)?'+_CLOCK+
    r'|在第?\s*'+_NUMBER+r'\s*秒(?:处)?)\s*[,，:：;；–—-]\s*(?P<first>[^\W\d_])?',re.IGNORECASE)
_LABEL=r'(?:source|video|footage|timestamp|timecode|nguồn|mốc)\s*:?\s*'
_MARK=_CLOCK+r'(?:\s*[-–—]\s*'+_CLOCK+r')?'
_ANNOTATION=re.compile(r'(?:\[\s*(?:'+_LABEL+r')?'+_MARK+r'\s*\]|\(\s*'+_LABEL+_MARK+r'\s*\))',re.IGNORECASE)


def clean_narration(text):
    def prefix(match):
        return ('' if match.start()==0 else ' ')+(match.group('first') or '').upper()
    result=_PREFIX.sub(prefix,text)
    result=_ANNOTATION.sub('',result)
    if result==text:
        return text
    result=re.sub(r'\s+([,.!?])',r'\1',re.sub(r'[ \t]{2,}',' ',result)).strip()
    return result[:1].upper()+result[1:]


def prepare_clean_narration(project, only_id=None):
    """Invalidate audio only when spoken text changes; keep original transcript."""
    edited=None
    for i,n in enumerate(project.get('narrations',[])):
        if not n['enabled'] or (only_id is not None and n['id']!=only_id):
            continue
        clean=clean_narration(n['text'])
        dirty_cues=any(clean_narration(c['text'])!=c['text'] for c in n.get('cues',[]))
        if clean==n['text'] and not dirty_cues:
            continue
        if not clean:
            raise ValueError(f'Lời kể {n["id"]} chỉ chứa mốc dẫn chứng. Viết nội dung diễn biến trước khi tạo giọng.')
        if edited is None:edited=copy.deepcopy(project)
        new=edited['narrations'][i]
        new.update(text=clean,cues=[],caption_version=0)
        if clean!=n['text']:
            new.update(audio='',audio_hash='',duration=0)
            for plan in (edited.get('story_plan') or {}, (edited.get('duration_plan') or {}).get('schedule') or {}):
                if plan:
                    from .hook_policy import set_text
                    set_text(plan,n.get('segment_id'),clean)
    if edited is None:return project
    from . import store
    edited.update(exports=[],preview_exports=[])
    return store.save(edited)
