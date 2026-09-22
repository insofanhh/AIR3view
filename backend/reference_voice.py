"""Automatic source-language reference transcription, cached by audio bytes."""
import hashlib
import json
import re
import uuid

from . import store


def _read_cache(path, fingerprint):
    try:
        cached = json.loads(path.read_text('utf-8'))
        if cached.get('version') == 1 and cached.get('sha256') == fingerprint:
            text = cached.get('text', '')
            if isinstance(text, str) and text.strip():
                return text.strip()
    except (OSError, ValueError, AttributeError):
        pass
    return ''


def ensure_transcript(project, report, check):
    from . import providers, preferences
    settings = project['settings']
    if settings.get('voice_mode') != 'clone':
        return
    relative = settings.get('voice_reference', '')
    if not relative:
        raise ValueError('Chọn audio giọng mẫu trước khi dùng chế độ giọng tham chiếu.')
    path = store.asset(project['id'], relative)
    if not path.is_file():
        raise ValueError('Không tìm thấy audio giọng mẫu. Hãy tải lại file tham chiếu.')
    check()
    digest = hashlib.sha256()
    with path.open('rb') as audio:
        for block in iter(lambda: audio.read(1024 * 1024), b''):
            check()
            digest.update(block)
    fingerprint = digest.hexdigest()
    cache = store.DATA / '_preferences' / 'reference-transcripts' / (fingerprint + '.json')
    text = _read_cache(cache, fingerprint)
    old_hash = settings.get('voice_reference_hash', '')
    if not text and settings.get('voice_reference_text', '').strip() and old_hash in ('', fingerprint):
        # Preserve an existing user-verified transcript from older projects.
        # Uploading a replacement clears both fields; a same-path byte change
        # with a recorded hash forces recognition again.
        text = settings['voice_reference_text'].strip()
    if not text:
        report(2, 'Tự nhận dạng lời trong giọng mẫu…')
        # No expected_text: media.transcribe detects the sample's language,
        # independently of the desired output video language.
        cues = providers.transcribe(path, dict(settings), check)
        check()
        text = ' '.join(cue.get('text', '').strip() for cue in cues if cue.get('text', '').strip())
        meaningful = re.sub(r'\[[^\]]*\]|\([^)]*\)|[♪♫]', '', text)
        if not re.search(r'\w', meaningful, re.UNICODE):
            raise ValueError('Không nhận dạng được lời trong giọng mẫu. Hãy thay bằng audio có giọng nói rõ, ít nhạc nền.')
    check()
    # Cache first: a later TTS failure/restart must not repeat ASR.
    cache.parent.mkdir(parents=True, exist_ok=True)
    if _read_cache(cache, fingerprint) != text:
        temporary = cache.with_name(cache.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps({'version': 1, 'sha256': fingerprint, 'text': text}, ensure_ascii=False), 'utf-8')
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
    check()
    changed = settings.get('voice_reference_text') != text or old_hash != fingerprint
    settings.update(voice_reference_text=text, voice_reference_hash=fingerprint)
    if changed:
        store.save(project)
    preferences.update_reference_transcript(fingerprint, text)
