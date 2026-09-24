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
        db.execute("UPDATE jobs SET state='interrupted', message='Ứng dụng đã khởi động lại. Chọn Thử lại để tiếp tục.' WHERE state IN ('running','queued')")
        for row in db.execute('SELECT id, body FROM projects').fetchall():
            project = json.loads(row['body'])
            settings = project.setdefault('settings', {})
            tts_changed = False
            if 'tts_provider' not in settings:
                settings['tts_provider'] = 'vieneu'
                tts_changed = True
            if 'vieneu_url' not in settings:
                settings['vieneu_url'] = 'http://localhost:7860'
                tts_changed = True
            if 'production_workflow' not in settings:
                settings['production_workflow'] = 'legacy'
                settings['duration_min_ratio'] = .75
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
    settings.update(production_workflow='plan_first', duration_min_ratio=.9)
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
    return any(j['state'] in ('queued', 'running') for j in jobs(pid))


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
        db.execute('UPDATE preferences SET source_project_id=NULL WHERE source_project_id=?',(pid,))
    return {'deleted':True,'id':pid}


def update_job(jid, **values):
    allowed = {'state', 'progress', 'message', 'error', 'cancelled'}
    if not values or not set(values) <= allowed:
        raise ValueError('Invalid job update')
    with conn() as db:
        db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), jid])


def asset(pid, relative):
    root = project_dir(pid).resolve()
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError('Đường dẫn asset không hợp lệ.')
    return result
