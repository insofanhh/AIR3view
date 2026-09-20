from backend.alignment import align_script


def test_correct_spelling_while_retaining_asr_anchors():
    words=[{'text':'Phần','start':0,'end':.4},{'text':'mở','start':.4,'end':.7},{'text':'đầu','start':.7,'end':1},{'text':'đảo','start':1,'end':1.4},{'text':'móc','start':1.4,'end':1.8},{'text':'thời','start':1.8,'end':2.1},{'text':'gian','start':2.1,'end':2.5}]
    result=align_script('Phần mở đầu đảo mốc thời gian.',words,3)
    assert ' '.join(c['text'] for c in result)=='Phần mở đầu đảo mốc thời gian.'
    assert result[0]['start']==0
    assert result[-1]['end']==2.5


def test_wrong_language_falls_back_to_exact_utterance_text():
    text='Eric giơ hai tay khi cảnh sát làm rõ tình huống.'
    cues=align_script(text,[{'text':'unrelated language','start':0,'end':3}],3.4)
    assert cues==[{'id':'c0','start':0,'end':3.4,'text':text,'speaker':'ai'}]


def test_gaps_split_subtitles_instead_of_filling_silence():
    words=[{'text':'Xin','start':0,'end':.4},{'text':'chào','start':.4,'end':.8},{'text':'mọi','start':3,'end':3.4},{'text':'người','start':3.4,'end':3.9}]
    cues=align_script('Xin chào mọi người',words,4)
    assert len(cues)==2
    assert cues[0]['end']==.8 and cues[1]['start']==3
