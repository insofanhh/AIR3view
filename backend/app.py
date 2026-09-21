import asyncio
import json
import os
import queue
import re
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import store, providers
from .models import Model, ProjectEdit, Settings
from .media import prepare, transcribe, parse_srt, Cancelled, FFMPEG
from .render import render
from .timeline import build
from .power import keep_awake

QUEUE = queue.Queue()
STOP = threading.Event()


def work():
    while not STOP.is_set():
        try:
            jid = QUEUE.get(timeout=.5)
        except queue.Empty:
            continue
        def check():
            if STOP.is_set() or store.job(jid)['cancelled']:
                raise Cancelled('Đã hủy tác vụ; dữ liệu hoàn thành vẫn được giữ.')
        def report(progress, message):
            check()
            store.update_job(jid, progress=progress, message=message)
        try:
            with keep_awake():
                record = store.job(jid)
                check()
                store.update_job(jid, state='running', message='Đang bắt đầu…')
                project = store.read(record['project_id'])
                kind = record['kind']
                if kind == 'all':
                    from .story import output_budget
                    output_budget(project['settings'])
                if kind == 'prepare' or (kind == 'all' and not project.get('frames')):
                    project = prepare(project, report, check)
                if kind in ('transcribe',):
                    if not project.get('metadata', {}).get('has_audio'):
                        raise ValueError('Nguồn không có audio để nhận dạng.')
                    report(10, 'Nhận dạng thoại gốc… Lần đầu sẽ tải model ASR.')
                    project['transcript'] = transcribe(store.project_dir(project['id']) / 'audio.wav', project['settings'], check)
                    project['source_transcript'] = [dict(c) for c in project['transcript']]
                    project['transcript_language'] = ''
                    project['exports'] = []
                    store.save(project)
                if kind in ('analyze', 'all'):
                    project = providers.analyze(project, report, check)
                if kind in ('localize', 'language', 'analyze', 'all', 'voice') or kind.startswith('voice:'):
                    project = providers.localize(project, report, check)
                if kind in ('voice', 'language', 'all') or kind.startswith('voice:'):
                    if kind == 'all' and not project['narrations']:
                        project['warnings'].append('Đoạn quá ngắn để đề xuất lời dẫn; bản dựng chỉ có tiếng gốc. Có thể thêm lời AI thủ công.')
                    else:
                        project = providers.synthesize(project, report, check, kind.split(':', 1)[1] if ':' in kind else None)
                if kind == 'captions':
                    from .captions import refresh
                    project = refresh(project, report, check)
                if kind in ('render', 'preview', 'all'):
                    if kind in ('render', 'preview'):
                        project = providers.prepare_render_audio(project, report, check)
                    if project['settings'].get('subtitle_highlight', True):
                        build(project, strict=True)
                        from .captions import refresh
                        project = refresh(project, report, check)
                    render(project, report, check, preview=kind == 'preview')
                check()
                store.update_job(jid, state='completed', progress=100, message='Hoàn tất')
        except Cancelled as e:
            store.update_job(jid, state='cancelled', message=str(e))
        except Exception as e:
            message = str(e).replace(providers.key(), '[KEY]') if providers.key() else str(e)
            message = re.sub(r'sk-[A-Za-z0-9_-]+', '[KEY]', message)
            store.update_job(jid, state='failed', message='Tác vụ chưa hoàn tất', error=message[-5000:])
        finally:
            QUEUE.task_done()


@asynccontextmanager
async def lifespan(app):
    store.init()
    STOP.clear()
    worker = threading.Thread(target=work, daemon=True, name='air3view-worker')
    worker.start()
    yield
    STOP.set()
    worker.join(timeout=3)


app = FastAPI(title='AIR3view Studio', version='0.1.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])


@app.middleware('http')
async def local_mutations(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if request.headers.get('x-air3view') != 'studio' or (origin and urlparse(origin).hostname not in ('127.0.0.1', 'localhost', '::1')):
            return JSONResponse({'detail': 'Yêu cầu phải đến từ AIR3view trên máy này.'}, status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=422)


@app.exception_handler(KeyError)
async def key_error(request, exc):
    return JSONResponse({'detail': 'Không tìm thấy dữ liệu.'}, status_code=404)


class URLInput(Model):
    url: str


class KeyInput(Model):
    api_key: str


class JobInput(Model):
    kind: str


def queue_job(pid, kind):
    if kind not in ('prepare', 'transcribe', 'analyze', 'localize', 'language', 'voice', 'captions', 'render', 'preview', 'all') and not re.fullmatch(r'voice:[a-zA-Z0-9_-]+', kind):
        raise ValueError('Loại tác vụ không hợp lệ.')
    project = store.read(pid)
    if kind in ('all', 'analyze', 'render', 'preview'):
        from .story import output_budget
        output_budget(project['settings'])
    if project['settings'].get('output_mode') and (kind in ('voice', 'language') or kind.startswith('voice:')):
        from .story import plan_is_current
        if not plan_is_current(project):
            raise ValueError('Cấu hình đầu ra đã thay đổi. Phân tích AI lại trước khi tạo giọng để đọc đúng kịch bản mới.')
    jid = store.new_job(pid, kind)
    QUEUE.put(jid)
    return store.job(jid)


def editable(pid):
    if store.busy(pid):
        raise HTTPException(409, 'Dự án đang xử lý. Hãy đợi hoặc hủy trước khi sửa.')


@app.get('/api/health')
def health():
    return {'ok': True, 'ffmpeg': bool(shutil.which(FFMPEG) or Path(FFMPEG).is_file()), 'codex': bool(providers.codex_binary()), 'api_key': bool(providers.key()), 'version': '0.1.0'}


@app.get('/api/omnivoice')
def omnivoice_status():
    try:
        response = requests.get('http://127.0.0.1:8001/config', timeout=3)
        response.raise_for_status()
        config = response.json()
        return {'ok': True, 'title': config.get('title'), 'endpoints': [d['api_name'] for d in config.get('dependencies', [])]}
    except Exception:
        return {'ok': False, 'message': 'Không kết nối được OmniVoice ở cổng 8001.'}


@app.post('/api/key')
def set_key(body: KeyInput):
    providers.SESSION_KEY = body.api_key.strip()
    return {'configured': bool(providers.key()), 'storage': 'session'}


def present_project(project):
    # Metadata is saved before proxy generation. Do not advertise a playable
    # source until the temporary file has been atomically renamed.
    preview = None
    path = store.project_dir(project['id']) / 'proxy.mp4'
    try:
        stat = path.stat()
        if stat.st_size:
            preview = {'file': path.name, 'version': f'{stat.st_mtime_ns}-{stat.st_size}'}
    except FileNotFoundError:
        pass
    return {**project, 'preview': preview, 'shared_voice': providers.shared_voice(project)}


@app.get('/api/projects')
def list_projects():
    return [present_project(p) for p in store.list_projects()]


@app.post('/api/projects/url')
def import_url(body: URLInput):
    parsed = urlparse(body.url.strip())
    if parsed.scheme != 'https' or parsed.hostname not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtu.be'):
        raise ValueError('Nhập link HTTPS của YouTube hoặc youtu.be.')
    project = store.create('Video YouTube mới', {'kind': 'youtube', 'url': body.url.strip(), 'file': ''})
    queue_job(project['id'], 'prepare')
    return project


@app.post('/api/projects/upload')
async def upload_video(file: UploadFile = File(...)):
    ext = Path(file.filename or '').suffix.lower()
    if ext not in ('.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'):
        raise ValueError('Chọn video MP4, MOV, MKV, WEBM hoặc AVI.')
    project = store.create(Path(file.filename).stem[:180], {'kind': 'upload', 'file': 'source' + ext})
    destination = store.project_dir(project['id']) / ('source' + ext)
    total = 0
    with destination.open('wb') as output:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > 10 * 1024**3:
                output.close()
                destination.unlink(missing_ok=True)
                raise ValueError('File vượt giới hạn 10 GB.')
            output.write(chunk)
    await file.close()
    queue_job(project['id'], 'prepare')
    return project


@app.get('/api/projects/{pid}')
def get_project(pid: str):
    return present_project(store.read(pid))


@app.put('/api/projects/{pid}')
def edit_project(pid: str, body: ProjectEdit):
    with store.LOCK:
        editable(pid)
        project = store.read(pid)
        if body.revision != project['revision']:
            raise HTTPException(409, 'Dự án đã thay đổi. Tải lại trước khi lưu để không ghi đè bản mới.')
        incoming = body.model_dump()
        ids = [n['id'] for n in incoming['narrations']]
        if len(ids) != len(set(ids)):
            raise ValueError('ID lời dẫn phải khác nhau.')
        for n in incoming['narrations']:
            if n['audio']:
                store.asset(pid, n['audio'])
        if incoming['settings']['voice_reference']:
            store.asset(pid, incoming['settings']['voice_reference'])
        # Limit server-side TTS requests to the explicitly configured local service.
        tts_url = urlparse(incoming['settings']['omnivoice_url'])
        if tts_url.scheme != 'http' or tts_url.hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError('Bản local chỉ kết nối OmniVoice qua HTTP localhost.')
        def clear_stale_words(new_cues, old_cues):
            old_by_id = {c['id']: c for c in old_cues}
            changed = False
            for cue in new_cues:
                old = old_by_id.get(cue['id'])
                if old and any(cue[k] != old[k] for k in ('text', 'start', 'end')):
                    if cue.get('words', []) == old.get('words', []):
                        cue['words'] = []
                    changed = True
            return changed
        clear_stale_words(incoming['transcript'], project['transcript'])
        old_narrations = {n['id']: n for n in project['narrations']}
        for n in incoming['narrations']:
            old = old_narrations.get(n['id'])
            if old and clear_stale_words(n['cues'], old.get('cues', [])):
                n['caption_version'] = 0
        from .story import plan_is_current, plan_fingerprint
        if plan_is_current(project):
            # Migrate a verified old signature before applying edits. Never
            # bless already stale plans or recompute from changed settings.
            project['plan_fingerprint'] = plan_fingerprint(project)
        project.update(incoming)
        project['exports'] = []
        project['preview_exports'] = []
        build(project)  # validate ranges before persisting
        return present_project(store.save(project))


@app.get('/api/projects/{pid}/timeline')
def timeline(pid: str):
    return build(store.read(pid))


@app.get('/api/projects/{pid}/jobs')
def project_jobs(pid: str):
    return store.jobs(pid)


@app.post('/api/projects/{pid}/jobs')
def start_job(pid: str, body: JobInput):
    return queue_job(pid, body.kind)


@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: str):
    record = store.job(jid)
    if record['state'] in ('queued', 'running'):
        store.update_job(jid, cancelled=1, message='Đang hủy…')
    return store.job(jid)


@app.post('/api/jobs/{jid}/retry')
def retry_job(jid: str):
    record = store.job(jid)
    return queue_job(record['project_id'], record['kind'])


@app.post('/api/projects/{pid}/subtitles')
async def upload_subtitles(pid: str, file: UploadFile = File(...)):
    content = await file.read(5 * 1024**2 + 1)
    if len(content) > 5 * 1024**2:
        raise ValueError('File phụ đề quá lớn.')
    cues = parse_srt(content.decode('utf-8-sig'))
    with store.LOCK:
        editable(pid)
        project = store.read(pid)
        project.update(transcript=cues, source_transcript=[dict(c) for c in cues], transcript_language='', transcript_origin='imported_srt', exports=[], preview_exports=[])
        return present_project(store.save(project))


@app.post('/api/projects/{pid}/reference')
async def upload_reference(pid: str, file: UploadFile = File(...)):
    content = await file.read(50 * 1024**2 + 1)
    if len(content) > 50 * 1024**2:
        raise ValueError('Giọng mẫu tối đa 50 MB.')
    ext = Path(file.filename or '').suffix.lower()
    if ext not in ('.wav', '.mp3', '.m4a', '.ogg', '.flac'):
        raise ValueError('Chọn file audio giọng mẫu.')
    with store.LOCK:
        editable(pid)
        project = store.read(pid)
        relative = 'reference-' + uuid.uuid4().hex + ext
        store.asset(pid, relative).write_bytes(content)
        project['settings']['voice_reference'] = relative
        project['settings']['voice_reference_text'] = ''
        project['exports'] = []
        project['preview_exports'] = []
        return present_project(store.save(project))


@app.get('/api/projects/{pid}/document')
def project_document(pid: str):
    project = store.read(pid)
    return JSONResponse(project, headers={'Content-Disposition': f'attachment; filename="air3view-{pid[:8]}.json"'})


@app.get('/media/{pid}/{relative:path}')
def get_media(pid: str, relative: str):
    path = store.asset(pid, relative)
    if path.suffix.lower() not in ('.mp4', '.mov', '.mkv', '.webm', '.avi', '.wav', '.mp3', '.m4a', '.ogg', '.flac', '.jpg', '.png', '.srt', '.ass') or not path.is_file():
        raise HTTPException(404, 'Không tìm thấy file media.', headers={'Cache-Control': 'no-store'})
    return FileResponse(path, headers={'Cache-Control': 'no-cache'})


dist = store.ROOT / 'frontend' / 'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=str(dist), html=True), name='frontend')
else:
    @app.get('/')
    def frontend_help():
        return {'message': 'Chạy npm run build trong frontend, sau đó khởi động lại server; hoặc mở Vite localhost:5173.'}
