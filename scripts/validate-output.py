"""Read-only validation of a finished continuous-playback export on localhost."""
import json
import sys
from pathlib import Path
import av
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
pid = sys.argv[1]
base = f'http://127.0.0.1:8000/api/projects/{pid}'
p = requests.get(base, timeout=15).json()
t = requests.get(base + '/timeline', timeout=15).json()
assert p['settings']['narration_mode'] == 'overlay'
assert p['settings']['duck_volume'] == 0
assert all(c['kind'] != 'freeze' for c in t['clips'])
original = [c for c in t['clips'] if c['kind'] == 'original']
if p['settings'].get('output_mode'):
    from backend.story import output_budget
    count, seconds = output_budget(p['settings'])
    assert len(t['parts']) == count
    selected = [c for c in t['clips'] if c['kind'] == 'highlight']
    assert len(selected) == len(p['story_plan']['selections'])
    assert all(part['duration'] <= seconds + .1 for part in t['parts'])
    assert all(abs(c['source_start']-s['start']) < .04 and abs(c['source_end']-s['end']) < .04 for c,s in zip(selected,p['story_plan']['selections']))
else:
    assert len(original) == 1 and original[0]['source_start'] == 0
    assert abs(original[0]['source_end'] - p['metadata']['duration']) < 1/30 + .001
assert len(p['exports']) == len(t['parts']) and p['exports']
rows = []
for output, part in zip(p['exports'], t['parts']):
    path = ROOT / 'data' / pid / output['file']
    with av.open(str(path)) as c:
        v, a = c.streams.video[0], c.streams.audio[0]
        seconds = c.duration / av.time_base
        assert (v.width, v.height) == (1080, 1920)
        assert abs(seconds - part['duration']) < .1
        assert str(v.average_rate) == '30'
        rows.append(dict(file=output['file'], duration=seconds, video=v.codec_context.name, audio=a.codec_context.name, bytes=path.stat().st_size))
    if '--decode' in sys.argv:
        counts = {'video_frames': 0, 'audio_frames': 0}
        with av.open(str(path)) as container:
            for frame in container.decode(video=0, audio=0):
                counts['video_frames' if isinstance(frame, av.VideoFrame) else 'audio_frames'] += 1
        assert counts['video_frames'] >= round(part['duration'] * 30) - 2
        assert counts['audio_frames'] > 0
        rows[-1].update(counts)
    ass = (ROOT / 'data' / pid / output['ass']).read_text('utf-8-sig')
    if p['settings']['language'] == 'English' and p['settings'].get('output_mode') != 'single':
        assert f'PART {part["index"]}' in ass and 'PHẦN ' not in ass
print(json.dumps({'project': pid, 'source_seconds': p['metadata']['duration'], 'output_seconds': t['duration'], 'language': p['settings']['language'], 'narrations': len(t['voices']), 'no_freeze': True, 'source_mute_configured': True, 'fully_decoded': '--decode' in sys.argv, 'outputs': rows}, ensure_ascii=True, indent=2))
