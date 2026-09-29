import json
import os
import sqlite3
import threading
import time
import uuid
import shutil
import stat
from pathlib import Path
from .models import Settings

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('AIR3VIEW_DATA', str(ROOT / 'data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'studio.sqlite3'
LOCK = threading.RLock()


def conn():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def init():
    with conn() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, state TEXT, progress REAL, message TEXT, error TEXT, created REAL, cancelled INTEGER DEFAULT 0)')
        if 'options' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
            db.execute("ALTER TABLE jobs ADD COLUMN options TEXT NOT NULL DEFAULT '{}'")
        db.execute("UPDATE jobs SET state='interrupted', message='Ứng dụng đã khởi động lại. Chọn Thử lại để tiếp tục.' WHERE state='running'")
        db.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, settings TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS batch_items (id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, position INTEGER NOT NULL, project_id TEXT NOT NULL, url TEXT NOT NULL, state TEXT NOT NULL, job_id TEXT, error TEXT NOT NULL DEFAULT \'\', created REAL NOT NULL)')
        batch_columns = {row['name'] for row in db.execute('PRAGMA table_info(batch_items)')}
        if 'title' not in batch_columns:
            db.execute("ALTER TABLE batch_items ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        if 'exports' not in batch_columns:
            db.execute("ALTER TABLE batch_items ADD COLUMN exports TEXT NOT NULL DEFAULT '[]'")
        if 'attempts' not in batch_columns:
            db.execute('ALTER TABLE batch_items ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
        if 'ready_at' not in batch_columns:
            db.execute('ALTER TABLE batch_items ADD COLUMN ready_at REAL NOT NULL DEFAULT 0')
        if 'export_job_id' not in batch_columns:
            db.execute('ALTER TABLE batch_items ADD COLUMN export_job_id TEXT')
        if 'export_folder' not in batch_columns:
            db.execute("ALTER TABLE batch_items ADD COLUMN export_folder TEXT NOT NULL DEFAULT ''")
        db.execute('CREATE INDEX IF NOT EXISTS batch_items_batch ON batch_items(batch_id, position)')
        for row in db.execute('SELECT id, body FROM projects').fetchall():
            project = json.loads(row['body'])
            settings = project.setdefault('settings', {})
            tts_changed = False
            if 'tts_provider' not in settings:
                settings['tts_provider'] = 'vieneu'
                tts_changed = True
            if 'vieneu_device' not in settings:
                settings['vieneu_device'] = 'cpu'
                tts_changed = True
            if 'production_workflow' not in settings:
                settings['production_workflow'] = 'legacy'
                settings['duration_min_ratio'] = .75
                tts_changed = True
            if settings.get('output_mode') == 'parts':
                # Previous versions asked AI to write separate stories per part.
                # Keep old exports accessible while future analyses use one
                # complete story, then split only at export time.
                old_count = int(settings.get('part_count', 3))
                settings['summary_seconds'] = min(1800, max(10,
                    old_count * float(settings.get('part_seconds', 60))))
                settings['export_mode'] = 'parts' if old_count > 1 else 'single'
                settings['export_part_count'] = max(2, min(100, old_count))
                settings['output_mode'] = 'single'
                project.setdefault('warnings', []).append(
                    'Chế độ nhiều phần cũ đã chuyển sang chia lúc xuất. Phân tích lại để tạo một kịch bản hoàn chỉnh trước khi dựng mới.')
                tts_changed = True
            if project.get('playback_version', 0) < 2:
                settings.update(narration_mode='overlay', duck_volume=0)
                project.update(playback_version=2, exports=[], preview_exports=[])
                project['warnings'] = [w for w in project.get('warnings', []) if 'chèn dừng hình' not in w]
                tts_changed = True
            if tts_changed:
                project['revision'] += 1
                db.execute('UPDATE projects SET body=? WHERE id=?', (json.dumps(project, ensure_ascii=False), row['id']))
    from . import preferences
    preferences.initialize()


def project_dir(pid):
    if len(pid) != 32 or any(c not in '0123456789abcdef' for c in pid):
        raise ValueError('ID dự án không hợp lệ.')
    path = DATA / pid
    path.mkdir(exist_ok=True)
    return path


def read(pid):
    with conn() as db:
        row = db.execute('SELECT body FROM projects WHERE id=?', (pid,)).fetchone()
    if not row:
        raise KeyError(pid)
    return json.loads(row['body'])


def save(project):
    project['updated'] = time.time()
    project['revision'] = project.get('revision', 0) + 1
    with LOCK, conn() as db:
        db.execute('INSERT OR REPLACE INTO projects VALUES (?,?,?)', (project['id'], json.dumps(project, ensure_ascii=False), project['updated']))
    return project


def create(name, source):
    pid = uuid.uuid4().hex
    project_dir(pid)
    settings = Settings(output_mode='single', narration_style='storytelling', opening_delay=0).model_dump()
    settings.update(production_workflow='plan_first', duration_min_ratio=.9)
    from . import preferences
    settings = preferences.settings_for_project(pid, settings)
    settings.update(output_mode='single', production_workflow='plan_first', duration_min_ratio=.9)
    return save({'id': pid, 'name': name, 'source': source, 'created': time.time(), 'playback_version': 2, 'settings': settings, 'metadata': {}, 'scenes': [], 'frames': [], 'transcript': [], 'narrations': [], 'hooks': [], 'summary': '', 'exports': [], 'warnings': [], 'revision': 0})


def list_projects():
    with conn() as db:
        return [json.loads(x['body']) for x in db.execute('SELECT body FROM projects ORDER BY updated DESC')]


def job(jid):
    with conn() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not row:
        raise KeyError(jid)
    return dict(row)


def jobs(pid):
    with conn() as db:
        return [dict(x) for x in db.execute('SELECT * FROM jobs WHERE project_id=? ORDER BY created DESC LIMIT 20', (pid,))]


def busy(pid):
    if any(j['state'] in ('queued', 'running') for j in jobs(pid)):
        return True
    with conn() as db:
        return bool(db.execute("SELECT 1 FROM batch_items WHERE project_id=? AND state IN ('pending','queued','running')", (pid,)).fetchone())


def new_job(pid, kind, options=None):
    with LOCK, conn() as db:
        if not db.execute('SELECT 1 FROM projects WHERE id=?',(pid,)).fetchone():
            raise KeyError(pid)
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND state IN ('queued','running')", (pid,)).fetchone():
            raise ValueError('Dự án đang xử lý. Hãy đợi hoặc hủy tác vụ trước.')
        jid = uuid.uuid4().hex
        db.execute('INSERT INTO jobs (id,project_id,kind,state,progress,message,error,created,cancelled,options) VALUES (?,?,?,?,?,?,?,?,0,?)',
                   (jid, pid, kind, 'queued', 0, 'Đang chờ xử lý', '', time.time(),json.dumps(options or {})))
    return jid


def delete_project(pid, revision):
    """Delete only a verified project directory; never follow reparse points."""
    if len(pid)!=32 or any(c not in '0123456789abcdef' for c in pid):
        raise ValueError('ID dự án không hợp lệ.')
    with LOCK, conn() as db:
        # Lock database writers too, so a new job cannot claim a stale project
        # between the active-job check and filesystem cleanup.
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT body FROM projects WHERE id=?',(pid,)).fetchone()
        if not row:raise KeyError(pid)
        project=json.loads(row['body'])
        if project['revision']!=revision:
            raise RuntimeError('Dự án vừa thay đổi. Đóng hộp thoại và chọn Xóa lại để xác nhận bản mới nhất.')
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND state IN ('queued','running')",(pid,)).fetchone():
            raise RuntimeError('Dự án đang xử lý. Hãy đợi tác vụ hoàn tất hoặc hủy trước khi xóa.')
        if db.execute("SELECT 1 FROM batch_items WHERE project_id=? AND state IN ('pending','queued','running')", (pid,)).fetchone():
            raise RuntimeError('Video thuộc lô đang chờ xử lý. Hãy hủy tác vụ trong bảng lô trước khi xóa.')
        root=DATA.resolve()
        directory=root/pid
        def verify(path):
            attributes=path.lstat()
            if path.is_symlink() or getattr(attributes,'st_file_attributes',0) & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
                raise ValueError('Không xóa dự án chứa liên kết thư mục/file. Kiểm tra các liên kết trước khi thử lại.')
            resolved=path.resolve()
            if not resolved.is_relative_to(directory.resolve()):
                raise ValueError('Đường dẫn xóa vượt ngoài thư mục dự án.')
        if directory.exists() or directory.is_symlink():
            # Check the root link before resolving it, then verify the final
            # absolute target is an immediate child of DATA, never DATA itself.
            verify(directory)
            if directory.resolve().parent!=root or directory.resolve()==root:
                raise ValueError('Đường dẫn xóa dự án không hợp lệ.')
            for parent,dirs,files in os.walk(directory,followlinks=False):
                for name in dirs+files:verify(Path(parent)/name)
            shutil.rmtree(directory)
        # Keep the record if filesystem cleanup fails, so deletion can retry.
        db.execute('DELETE FROM jobs WHERE project_id=?',(pid,))
        db.execute('DELETE FROM projects WHERE id=?',(pid,))
        db.execute("UPDATE batch_items SET title='Dự án đã xóa',exports='[]',job_id=NULL,state='deleted' WHERE project_id=?", (pid,))
        db.execute('UPDATE preferences SET source_project_id=NULL WHERE source_project_id=?',(pid,))
    return {'deleted':True,'id':pid}


def update_job(jid, **values):
    allowed = {'state', 'progress', 'message', 'error', 'cancelled'}
    if not values or not set(values) <= allowed:
        raise ValueError('Invalid job update')
    with conn() as db:
        db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), jid])


def claim_job(jid):
    """Ensure only one worker executes a queued job, including after recovery."""
    with conn() as db:
        result = db.execute("UPDATE jobs SET state='running',message='Đang bắt đầu…' WHERE id=? AND state='queued'", (jid,))
        return result.rowcount == 1


def asset(pid, relative):
    root = project_dir(pid).resolve()
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError('Đường dẫn asset không hợp lệ.')
    return result
