import json
import pytest
from backend import providers
from backend.models import Settings
from backend.voice_repair import NarrationReply, repair_text


@pytest.mark.parametrize('raw',[
    '{"text":"A clear narration."}',
    '```json\n{"text":"A clear narration."}\n```',
    '{"items":[{"id":"narration","text":"A clear narration."}]}',
    '{"words":["A","clear","narration."]}',
])
def test_real_provider_boundary_normalizes_reply(tmp_path,monkeypatch,raw):
    from backend import gemini
    monkeypatch.setattr(providers,'key',lambda *a:'fake-key-for-test')
    calls=[]
    def generate(*args):
        calls.append(1)
        return raw,{}
    monkeypatch.setattr(gemini,'generate',generate)
    settings=Settings(provider='gemini',model='test-model').model_dump()
    for _ in range(2):
        assert providers.ask_ai('Prompt',[],settings,tmp_path,lambda:None,NarrationReply)=={'text':'A clear narration.'}
    assert len(calls)==1  # canonical accepted cache reused


@pytest.mark.parametrize('raw',[
    '{"items":[{"id":"other","text":"No"}]}',
    '{"items":[{"id":"narration","text":"One"},{"id":"narration","text":"Two"}]}',
    '{"text":"Text","extra":"unexpected"}',
    '{"words":["entire sentence instead of word"]}',
    'not JSON', '{"text":""}',
])
def test_rejected_reply_logged_not_cached(tmp_path,monkeypatch,raw):
    from backend import gemini
    monkeypatch.setattr(providers,'key',lambda *a:'')
    monkeypatch.setattr(gemini,'generate',lambda *a:(raw,{}))
    with pytest.raises(ValueError):
        providers.ask_ai('Prompt',[],Settings(provider='gemini',model='test').model_dump(),tmp_path,lambda:None,NarrationReply)
    logs=list((tmp_path/'analysis-cache').glob('*.rejected.json'))
    assert len(logs)==1 and json.loads(logs[0].read_text('utf-8'))['response']==raw
    assert not list((tmp_path/'analysis-cache').glob('*.meta.json'))


def test_malformed_first_reply_repaired_through_real_provider_validation(tmp_path,monkeypatch):
    from backend import gemini
    monkeypatch.setattr(providers,'key',lambda *a:'')
    responses=iter(['{"items":[]}','{"text":"The dog runs toward the open field."}'])
    monkeypatch.setattr(gemini,'generate',lambda *a:(next(responses),{}))
    result=repair_text('The dog runs.','Evidence','English',8,4,
                       Settings(provider='gemini',model='test').model_dump(),tmp_path,lambda:None,providers.ask_ai)
    assert result=='The dog runs toward the open field.'
