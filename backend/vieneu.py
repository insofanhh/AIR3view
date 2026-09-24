"""Adapter for the local VieNeu-TTS Studio Gradio text synthesis endpoint."""
from pathlib import Path


def ensure_ready(client):
    """Fail before uploading/generating when VieNeu has no loaded model."""
    try:
        status = client.predict(api_name='/restore_ui_state')
    except Exception as exc:
        raise RuntimeError('Không kiểm tra được trạng thái model VieNeu. Mở VieNeu Studio và thử lại.') from exc
    message = str(status or '')
    if 'Chưa tải model' in message or 'tải model' in message.lower() and 'chưa' in message.lower():
        raise RuntimeError('VieNeu chưa nạp model. Mở VieNeu Studio, chọn model và bấm Load model trước khi tạo giọng.')
    return message


def synthesis_endpoint(settings):
    # VieNeu binds a hidden gr.State to each button: /wrapper is preset_mode,
    # /wrapper_1 is custom_mode. Passing audio does NOT switch that state.
    return '/wrapper_1' if settings['voice_mode'] == 'clone' else '/wrapper'


def parameters(client, settings, text, reference=None, reference_text=''):
    from gradio_client import handle_file
    endpoints = client.view_api(return_format='dict', print_info=False)['named_endpoints']
    api_name = synthesis_endpoint(settings)
    endpoint = endpoints.get(api_name)
    if not endpoint:
        raise ValueError(f'VieNeu không có API {api_name} tương thích. Kiểm tra phiên bản VieNeu-TTS Studio.')
    expected = ['param_0', 'param_1', 'param_2', 'param_3', 'param_5', 'param_6', 'param_7', 'param_8', 'param_9', 'param_10']
    if [p['parameter_name'] for p in endpoint['parameters']] != expected:
        raise ValueError('API VieNeu đã thay đổi tham số; cần cập nhật adapter trước khi tạo giọng.')
    voice = settings.get('vieneu_voice') or None
    if settings['voice_mode'] == 'clone' and not reference:
        raise ValueError('Chọn audio giọng tham chiếu trước khi tạo giọng VieNeu.')
    if not reference and not voice:
        raise ValueError('Chọn tên giọng có sẵn trong VieNeu hoặc tải audio giọng tham chiếu trước khi tạo giọng.')
    return dict(param_0=text, param_1=voice if not reference else None,
                param_2=handle_file(str(reference)) if reference else None,
                param_3=reference_text if reference else '', param_5='Standard (Một lần)',
                param_6=False, param_7=1,
                param_8=.4 if settings.get('production_workflow')=='plan_first' else .8,
                param_9=256, param_10=True)


def reference_clip(project, report, check):
    """VieNeu trims reference audio to 8s; transcribe the exact cropped WAV."""
    from . import store, providers
    from .media import probe, run, FFMPEG, transcribe
    settings = project['settings']
    source = store.asset(project['id'], settings['voice_reference'])
    if not source.is_file():
        raise ValueError('Không tìm thấy audio giọng mẫu cho VieNeu.')
    if probe(source)['duration'] <= 8:
        from .reference_voice import ensure_transcript
        ensure_transcript(project, report, check)
        return source, settings['voice_reference_text']
    fingerprint = providers.digest({'reference': __import__('hashlib').sha256(source.read_bytes()).hexdigest(), 'vieneu_clip': 1})
    folder = store.project_dir(project['id']) / 'vieneu-references'
    folder.mkdir(exist_ok=True)
    audio, transcript = folder / (fingerprint + '.wav'), folder / (fingerprint + '.txt')
    if not audio.exists():
        temporary = audio.with_suffix('.tmp.wav')
        run([FFMPEG, '-y', '-i', source, '-t', '7.5', '-ar', '24000', '-ac', '1', temporary], check_cancel=check)
        temporary.replace(audio)
    if not transcript.exists():
        report(2, 'Nhận dạng đoạn giọng mẫu ngắn cho VieNeu…')
        cues = transcribe(audio, settings, check)
        text = ' '.join(c['text'].strip() for c in cues).strip()
        if not text:
            raise ValueError('Không nhận dạng được đoạn đầu giọng mẫu VieNeu. Chọn audio rõ tiếng dài 3–8 giây.')
        transcript.write_text(text, encoding='utf-8')
    return audio, transcript.read_text(encoding='utf-8')
