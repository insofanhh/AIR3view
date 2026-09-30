import asyncio
import json
import os
import queue
import re
import shutil
import threading
import uuid
from datetime import datetime
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import store, providers, preferences, updates, batches, export_files, youtube_auth
from .models import Model, ProjectEdit, Settings
from pydantic import Field
from .media import prepare, transcribe, parse_srt, Cancelled, FFMPEG
from .render import render
from .timeline import build
from .power import keep_awake

QUEUE = queue.Queue()
STOP = threading.Event()
PREPARE_LIMIT = threading.Semaphore(2)
AI_LIMIT = threading.Semaphore(1)
VOICE_LIMIT = threading.Semaphore(1)
RENDER_LIMIT = threading.Semaphore(1)


def overall_progress(kind, stage, progress, previous=0):
    if kind != 'all':
        return progress
    base, span = {'prepare': (0, 15), 'analyze': (15, 40),
                  'localize': (55, 5), 'voice': (60, 20),
                  'render': (80, 19)}[stage]
    return max(previous, min(99, base + span * max(0, min(100, progress)) / 100))


@contextmanager
def stage_slot(semaphore, check):
    while not semaphore.acquire(timeout=.2):
        check()
    try:
        check()
        yield
    finally:
        semaphore.release()


def prepare_job_story(project, kind, report, check):
    from .story import can_resume_story
    check()
    if kind == 'all' and can_resume_story(project):
        report(88, 'Dùng lại kịch bản đã đạt; tiếp tục tạo giọng và dựng video…')
        return project
    return providers.analyze(project, report, check)


def work():
    while not STOP.is_set():
        try:
            jid = QUEUE.get(timeout=.5)
        except queue.Empty:
            continue
        stage = 'prepare'
        last_progress = 0
        kind = None
        def check():
            if STOP.is_set() or store.job(jid)['cancelled']:
                raise Cancelled('Đã hủy tác vụ; dữ liệu hoàn thành vẫn được giữ.')
        def report(progress, message):
            nonlocal last_progress
            check()
            progress = overall_progress(kind, stage, progress, last_progress)
            last_progress = progress
            store.update_job(jid, progress=progress, message=message)
        try:
            with keep_awake():
                record = store.job(jid)
                # Recovery may enqueue the same id twice; only one worker can
                # claim it, so a completed video is never rendered again.
                if not store.claim_job(jid):
                    continue
                check()
                project = store.read(record['project_id'])
                kind = record['kind']
                if kind == 'all':
                    from .story import output_budget
                    output_budget(project['settings'])
                if kind == 'prepare' or (kind == 'all' and not project.get('frames')):
                    stage = 'prepare'
                    with stage_slot(PREPARE_LIMIT, check):
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
                    stage = 'analyze'
                    with stage_slot(AI_LIMIT, check):
                        project = prepare_job_story(project, kind, report, check)
                if kind in ('localize', 'language', 'analyze', 'all', 'voice') or kind.startswith('voice:'):
                    stage = 'localize'
                    with stage_slot(AI_LIMIT, check):
                        project = providers.localize(project, report, check)
                if kind in ('voice', 'language', 'all') or kind.startswith('voice:'):
                    if kind == 'all' and not project['narrations']:
                        from .story import source_led
                        if source_led(project):
                            report(80,'Các cảnh đã đủ tiếng gốc phù hợp; bỏ bước tạo giọng AI.')
                        else:
                            project['warnings'].append('Đoạn quá ngắn để đề xuất lời dẫn; bản dựng chỉ có tiếng gốc. Có thể thêm lời AI thủ công.')
                    else:
                        stage = 'voice'
                        with stage_slot(VOICE_LIMIT, check):
                            project = providers.synthesize(project, report, check, kind.split(':', 1)[1] if ':' in kind else None)
                if kind == 'captions':
                    from .captions import refresh
                    project = refresh(project, report, check)
                if kind in ('render', 'preview', 'all', 'export'):
                    stage = 'render'
                    with stage_slot(RENDER_LIMIT, check):
                        options=json.loads(record.get('options') or '{}')
                        if kind == 'export':
                            project['settings'].update(export_mode=options.get('export_mode',project['settings'].get('export_mode','single')),
                                                       export_part_count=options.get('export_part_count',project['settings'].get('export_part_count',2)),
                                                       export_drive=options.get('export_drive',project['settings'].get('export_drive','')),
                                                       export_directory=options.get('export_directory',project['settings'].get('export_directory','')))
                            project['settings']=Settings.model_validate(project['settings']).model_dump()
                            store.save(project)
                        if kind in ('render', 'preview', 'export'):
                            project = providers.prepare_render_audio(project, report, check)
                        if project['settings'].get('subtitle_highlight', True):
                            build(project, strict=True)
                            from .captions import refresh
                            project = refresh(project, report, check)
                        render(project, report, check, preview=kind == 'preview',preview_start=options.get('preview_start',0))
                        if kind == 'export':
                            report(99, 'Sao chép MP4 và phụ đề sang thư mục đã chọn…')
                            published=export_files.publish(project, project['settings'].get('export_drive',''),
                                                            options.get('batch_id'), check, options.get('export_date'),
                                                            project['settings'].get('export_directory',''))
                            project['export_folder']=published['folder']
                            project['exported_files']=published['files']
                            store.save(project)
                            if options.get('batch_id'):
                                with store.conn() as db:
                                    db.execute('UPDATE batch_items SET export_folder=? WHERE batch_id=? AND project_id=?',
                                               (published['folder'],options['batch_id'],project['id']))
                check()
                store.update_job(jid, state='completed', progress=100, message='Hoàn tất')
        except Cancelled as e:
            if STOP.is_set() and not store.job(jid)['cancelled']:
                store.update_job(jid, state='interrupted', message='Ứng dụng đã dừng; lô sẽ tự tiếp tục khi mở lại.')
            else:
                store.update_job(jid, state='cancelled', message=str(e))
        except Exception as e:
            message = providers.redact(str(e))
            message = re.sub(r'sk-[A-Za-z0-9_-]+', '[KEY]', message)
            store.update_job(jid, state='failed', message='Tác vụ chưa hoàn tất', error=message[-5000:])
        finally:
            QUEUE.task_done()


@asynccontextmanager
async def lifespan(app):
    store.init()
    STOP.clear()
    with store.conn() as db:
        queued = [row['id'] for row in db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created")]
    for jid in queued:
        QUEUE.put(jid)
    workers = [threading.Thread(target=work, daemon=True, name=f'air3view-worker-{i}') for i in range(2)]
    for worker in workers:
        worker.start()
    scheduler = threading.Thread(target=batches.run_scheduler, args=(STOP, QUEUE.put), daemon=True,
                                 name='air3view-batch-scheduler')
    scheduler.start()
    yield
    STOP.set()
    scheduler.join(timeout=3)
    for worker in workers:
        worker.join(timeout=3)
    from .vieneu import _runtime
    _runtime.close()


app = FastAPI(title='AIR3view Studio', version=updates.installed_version(), lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])


@app.middleware('http')
async def local_mutations(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if updates.INSTALL_GUARD.is_set():
            return JSONResponse({'detail': 'AIR3view đang cài bản cập nhật; hãy đợi ứng dụng mở lại.'}, status_code=503)
        origin = request.headers.get('origin')
        if request.headers.get('x-air3view') != 'studio' or (origin and urlparse(origin).hostname not in ('127.0.0.1', 'localhost', '::1')):
            return JSONResponse({'detail': 'Yêu cầu phải đến từ AIR3view trên máy này.'}, status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': providers.redact(str(exc))}, status_code=422)


@app.exception_handler(KeyError)
async def key_error(request, exc):
    return JSONResponse({'detail': 'Không tìm thấy dữ liệu.'}, status_code=404)


class URLInput(Model):
    url: str


class BatchItemInput(Model):
    url: str
    title: str = ''
    settings: dict = Field(default_factory=dict)


class BatchInput(Model):
    name: str = Field(default='Lô video YouTube', max_length=180)
    items: list[BatchItemInput] = Field(min_length=1, max_length=batches.MAX_ITEMS)
    settings: Settings
    source_project_id: str | None = None
    reference_token: str | None = None


class BatchPreviewInput(Model):
    items: list[BatchItemInput] = Field(min_length=1, max_length=batches.MAX_ITEMS)
    settings: Settings


class BatchActionInput(Model):
    action: Literal['pause', 'resume', 'cancel', 'cancel_item', 'retry']
    item_id: str | None = None


class BatchExportInput(Model):
    item_id: str | None = None
    export_mode: Literal['single', 'parts'] = 'single'
    export_part_count: int = Field(default=2, ge=2, le=100)
    export_drive: str = Field(default='', pattern=r'^$|^[A-Za-z]:$')
    export_directory: str = Field(default='', max_length=1024)


class FolderPickerInput(Model):
    initial: str = Field(default='', max_length=1024)


class KeyInput(Model):
    provider: Literal['openai', 'gemini'] = 'openai'
    api_key: str


class YouTubeAuthInput(Model):
    mode: Literal['none', 'chrome', 'edge', 'file']


class GeminiCheckInput(Model):
    model: str


class JobInput(Model):
    kind: str
    preview_start: float = Field(default=0,ge=0)


class DeleteProjectInput(Model):
    revision: int
    confirm: Literal[True]


def queue_job(pid, kind, options=None):
    if updates.INSTALL_GUARD.is_set():
        raise ValueError('AIR3view đang cài bản cập nhật; không nhận tác vụ mới.')
    if kind not in ('prepare', 'transcribe', 'analyze', 'localize', 'language', 'voice', 'captions', 'render', 'preview', 'all', 'export') and not re.fullmatch(r'voice:[a-zA-Z0-9_-]+', kind):
        raise ValueError('Loại tác vụ không hợp lệ.')
    with store.conn() as db:
        if db.execute("SELECT 1 FROM batch_items WHERE project_id=? AND state IN ('pending','queued','running')", (pid,)).fetchone():
            raise ValueError('Video thuộc lô đang chờ xử lý. Hãy quản lý tác vụ trong bảng lô.')
    project = store.read(pid)
    if kind == 'export' and store.busy(pid):
        raise ValueError('Dự án đang có tác vụ khác. Đợi hoàn tất rồi xuất video.')
    if kind == 'export':
        destination = options or {}
        export_files.export_root(destination.get('export_drive', project['settings'].get('export_drive', '')),
                                 destination.get('export_directory', project['settings'].get('export_directory', '')))
    if kind in ('all', 'analyze', 'render', 'preview', 'export'):
        from .story import output_budget
        output_budget(project['settings'])
    if project['settings'].get('output_mode') and (kind in ('voice', 'language') or kind.startswith('voice:')):
        from .story import plan_is_current
        if not plan_is_current(project):
            raise ValueError('Cấu hình đầu ra đã thay đổi. Phân tích AI lại trước khi tạo giọng để đọc đúng kịch bản mới.')
    jid = store.new_job(pid, kind,options)
    if kind == 'export' and not (options or {}).get('batch_id'):
        destination = options or {}
        preferences.remember_export_destination(
            destination.get('export_directory', project['settings'].get('export_directory', '')),
            destination.get('export_drive', project['settings'].get('export_drive', '')))
    QUEUE.put(jid)
    return store.job(jid)


def editable(pid):
    if store.busy(pid):
        raise HTTPException(409, 'Dự án đang xử lý. Hãy đợi hoặc hủy trước khi sửa.')


@app.get('/api/health')
def health():
    return {'ok': True, 'ffmpeg': bool(shutil.which(FFMPEG) or Path(FFMPEG).is_file()), 'codex': bool(providers.codex_binary()), 'api_key': bool(providers.key()), 'gemini_api_key': bool(providers.key('gemini')), 'openai_key_source': providers.key_source('openai'), 'gemini_key_source': providers.key_source('gemini'), 'version': updates.installed_version(), 'voice_repair_version':providers.VOICE_REPAIR_VERSION, 'layout_schema_version': 2}


@app.get('/api/export/storage')
def export_storage():
    settings = preferences.status()['settings']
    return {**export_files.storage_options(),
            'recent': settings.get('export_directory', ''),
            'recent_drive': settings.get('export_drive', '')}


@app.post('/api/export/pick-directory')
def choose_export_directory(body: FolderPickerInput):
    return {'directory': export_files.pick_directory(body.initial)}


@app.get('/api/update')
def update_status(refresh: bool = False):
    return updates.check_updates(refresh=refresh)


@app.get('/api/update/download')
def update_download_status():
    return updates.download_status()


@app.post('/api/update/download')
def update_download():
    return updates.start_download()


@app.post('/api/update/install')
def update_install():
    return updates.start_install()


@app.get('/api/omnivoice')
def omnivoice_status():
    try:
        response = requests.get('http://127.0.0.1:8001/config', timeout=3)
        response.raise_for_status()
        config = response.json()
        return {'ok': True, 'title': config.get('title'), 'endpoints': [d['api_name'] for d in config.get('dependencies', [])]}
    except Exception:
        return {'ok': False, 'message': 'Không kết nối được OmniVoice ở cổng 8001.'}


@app.get('/api/tts')
def tts_status(provider: Literal['vieneu', 'omnivoice'] = 'vieneu', url: str = 'http://127.0.0.1:8001'):
    if provider == 'vieneu':
        from .vieneu import status
        return status()
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1') or parsed.username or parsed.password:
        raise ValueError('Dịch vụ giọng đọc phải dùng HTTP localhost.')
    try:
        response = requests.get(url.rstrip('/') + '/config', timeout=3)
        response.raise_for_status()
        config = response.json()
        endpoints = {x.get('api_name') for x in config.get('dependencies', [])}
        expected = {'_clone_fn', '_design_fn'}
        return {'ok': expected <= endpoints, 'provider': provider, 'title': config.get('title'),
                'message': 'Kết nối API; cần nạp model trong dịch vụ trước khi tạo giọng.'}
    except Exception:
        return {'ok': False, 'provider': provider, 'message': 'Không kết nối được dịch vụ giọng đọc.'}


@app.post('/api/gemini/models')
def gemini_models():
    from .gemini import list_models
    try:
        return {'models': list_models(providers.key('gemini'))}
    except RuntimeError as exc:
        raise HTTPException(502, providers.redact(str(exc))) from None


@app.post('/api/gemini/check')
def gemini_check(body: GeminiCheckInput):
    from .gemini import check_model
    try:
        return check_model(body.model, providers.key('gemini'))
    except RuntimeError as exc:
        raise HTTPException(502, providers.redact(str(exc))) from None


@app.post('/api/key')
def set_key(body: KeyInput):
    from . import credentials
    # Persist before changing the active session: a failed write must not look
    # like a successful save to the user.
    try:
        credentials.set_key(body.provider, body.api_key.strip())
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.provider == 'gemini':
        providers.GEMINI_SESSION_KEY = ''
    else:
        providers.SESSION_KEY = ''
    return {'provider': body.provider, 'configured': bool(providers.key(body.provider)),
            'storage': 'local_encrypted', 'source': providers.key_source(body.provider)}


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


@app.get('/api/preferences')
def get_preferences():
    return preferences.status()


@app.get('/api/youtube/auth')
def youtube_auth_status():
    return youtube_auth.status()


@app.put('/api/youtube/auth')
def set_youtube_auth(body: YouTubeAuthInput):
    return youtube_auth.set_mode(body.mode)


@app.post('/api/youtube/cookies')
async def upload_youtube_cookies(file: UploadFile = File(...)):
    content = await file.read(youtube_auth.MAX_COOKIE_BYTES + 1)
    return youtube_auth.save_cookie_file(content)


@app.get('/api/batches/defaults')
def batch_defaults():
    projects = store.list_projects()
    if projects:
        project = projects[0]
        saved = preferences.status()['settings']
        return {'settings': {**project['settings'], 'title': '',
                             'export_directory': saved.get('export_directory', ''),
                             'export_drive': saved.get('export_drive', '')},
                'source_project_id': project['id']}
    settings = Settings(output_mode='single', narration_style='storytelling', opening_delay=0,
                        production_workflow='plan_first', duration_min_ratio=.9).model_dump()
    settings.update(preferences.status()['settings'])
    settings['voice_reference'] = ''
    return {'settings': Settings.model_validate(settings).model_dump(), 'source_project_id': None}


@app.post('/api/batches/preview')
def preview_batch(body: BatchPreviewInput):
    result = []
    seen = set()
    for index, item in enumerate(body.items, 1):
        try:
            url = batches.canonical_url(item.url)
            if url in seen:
                raise ValueError('Link trùng trong danh sách.')
            if 'voice_reference' in item.settings:
                raise ValueError('Giọng mẫu dùng cấu hình chung, không đặt đường dẫn riêng cho từng video.')
            from .story import output_budget
            normalized = batches.effective_settings(body.settings.model_dump(), item.settings)
            output_budget(normalized)
            seen.add(url)
            result.append({'row': index, 'url': url, 'title': item.title, 'valid': True, 'error': ''})
        except ValueError as exc:
            result.append({'row': index, 'url': item.url, 'title': item.title, 'valid': False, 'error': str(exc)})
    return result


@app.post('/api/batches/import-excel')
async def import_batch_excel(file: UploadFile = File(...)):
    if Path(file.filename or '').suffix.lower() != '.xlsx':
        raise ValueError('Chọn file Excel .xlsx.')
    content = await file.read(2 * 1024 * 1024 + 1)
    await file.close()
    return await asyncio.to_thread(batches.parse_excel, content)


@app.post('/api/batches/reference')
async def upload_batch_reference(file: UploadFile = File(...)):
    content = await file.read(batches.MAX_REFERENCE_BYTES + 1)
    await file.close()
    return {'token': batches.save_reference(file.filename, content), 'name': Path(file.filename or '').name}


@app.get('/api/batches/template')
def batch_excel_template():
    from io import BytesIO
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Videos'
    sheet.append(['url', 'title', 'summary_minutes', 'editorial_mode', 'hook_enabled',
                  'export_mode', 'export_part_count'])
    sheet.append(['https://www.youtube.com/watch?v=EXiQCyqxmSE', 'Video mẫu', 3,
                  'reaction_cops', False, 'single', 2])
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return StreamingResponse(stream, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             headers={'Content-Disposition': 'attachment; filename="AIR3view-batch-template.xlsx"'})


@app.get('/api/batches')
def list_batches():
    return batches.list_batches()


@app.post('/api/batches')
def create_batch(body: BatchInput):
    return batches.create(body.name, [item.model_dump() for item in body.items],
                          body.settings.model_dump(), body.source_project_id, body.reference_token)


@app.get('/api/batches/{batch_id}')
def get_batch(batch_id: str):
    return batches.get(batch_id)


@app.post('/api/batches/{batch_id}/control')
def control_batch(batch_id: str, body: BatchActionInput):
    return batches.control(batch_id, body.action, body.item_id)


@app.post('/api/batches/{batch_id}/export')
def export_batch(batch_id: str, body: BatchExportInput):
    batch=batches.get(batch_id)
    selected=[item for item in batch['items'] if body.item_id is None or item['id']==body.item_id]
    if not selected or (body.item_id is None and len(selected)!=len(batch['items'])):
        raise ValueError('Không tìm thấy video trong lô.')
    if any(item['state']!='completed' for item in selected):
        raise ValueError('Chỉ xuất khi các video đã hoàn tất; xuất cả lô cần mọi video hoàn tất.')
    export_files.export_root(body.export_drive, body.export_directory)  # Validate destination before queuing work.
    if any(store.busy(item['project_id']) for item in selected):
        raise ValueError('Một video đang có tác vụ khác; đợi hoàn tất rồi xuất.')
    export_date=datetime.now().strftime('%Y-%m-%d')
    jobs=[]
    for item in selected:
        job=queue_job(item['project_id'],'export',{'export_mode':body.export_mode,
                      'export_part_count':body.export_part_count,'export_drive':body.export_drive,
                      'export_directory':body.export_directory,
                      'batch_id':batch_id,'export_date':export_date})
        with store.conn() as db:
            db.execute("UPDATE batch_items SET export_job_id=?,export_folder='' WHERE id=?",(job['id'],item['id']))
        jobs.append(job)
    preferences.remember_export_destination(body.export_directory, body.export_drive)
    return {'jobs':jobs,'batch':batches.get(batch_id)}


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


@app.delete('/api/projects/{pid}')
def delete_project(pid: str, body: DeleteProjectInput):
    try:
        return store.delete_project(pid,body.revision)
    except RuntimeError as exc:
        raise HTTPException(409,str(exc)) from exc
    except OSError as exc:
        raise HTTPException(409,'Chưa xóa hết file dự án; có thể file đang được sử dụng. Đóng trình phát/file liên quan rồi thử xóa lại. Dự án vẫn còn trong danh sách.') from exc


@app.post('/api/projects/{pid}/apply-preferences')
def apply_preferences(pid: str):
    with store.LOCK:
        editable(pid)
        project, changed = preferences.apply(store.read(pid))
        if changed:
            project = store.save(project)
        return present_project(project)


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
        # Limit server-side TTS requests to explicitly configured local services.
        for label, value in (('OmniVoice', incoming['settings']['omnivoice_url']),):
            tts_url = urlparse(value)
            if tts_url.scheme != 'http' or tts_url.hostname not in ('127.0.0.1', 'localhost'):
                raise ValueError(f'Bản local chỉ kết nối {label} qua HTTP localhost.')
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
        previous_settings = Settings.model_validate(project['settings']).model_dump()
        changed_settings = {key for key, value in incoming['settings'].items()
                            if value != previous_settings.get(key)}
        export_only = (changed_settings <= {'export_mode', 'export_part_count', 'export_drive', 'export_directory'}
                       and incoming['name'] == project.get('name', incoming['name'])
                       and incoming['narrations'] == project['narrations']
                       and incoming['transcript'] == project['transcript'])
        project.update(incoming)
        if not export_only:
            project['exports'] = []
            project['preview_exports'] = []
        build(project)  # validate ranges before persisting
        project = preferences.save_project(project)
        return present_project(project)


@app.get('/api/projects/{pid}/timeline')
def timeline(pid: str):
    return build(store.read(pid))


@app.get('/api/projects/{pid}/jobs')
def project_jobs(pid: str):
    return store.jobs(pid)


@app.post('/api/projects/{pid}/jobs')
def start_job(pid: str, body: JobInput):
    return queue_job(pid, body.kind,{'preview_start':body.preview_start} if body.kind=='preview' else {})


@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid: str):
    record = store.job(jid)
    if record['state'] in ('queued', 'running'):
        store.update_job(jid, cancelled=1, message='Đang hủy…')
    return store.job(jid)


@app.post('/api/jobs/{jid}/retry')
def retry_job(jid: str):
    record = store.job(jid)
    return queue_job(record['project_id'], record['kind'],json.loads(record.get('options') or '{}'))


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
        project['settings']['voice_reference_hash'] = ''
        project['exports'] = []
        project['preview_exports'] = []
        project = preferences.save_project(project)
        return present_project(project)


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
