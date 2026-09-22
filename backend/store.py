import json
import os
import sqlite3
import threading
import time
import uuid
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
        db.execute("UPDATE jobs SET state='interrupted', message='Ứng dụng đã khởi động lại. Chọn Thử lại để tiếp tục.' WHERE state IN ('running','queued')")
        for row in db.execute('SELECT id, body FROM projects').fetchall():
            project = json.loads(row['body'])
            if project.get('playback_version', 0) < 2:
                project['settings'].update(narration_mode='overlay', duck_volume=0)
                project.update(playback_version=2, exports=[], preview_exports=[])
                project['warnings'] = [w for w in project.get('warnings', []) if 'chèn dừng hình' not in w]
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
    from . import preferences
    settings = preferences.settings_for_project(pid, settings)
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


def new_job(pid, kind):
    with LOCK, conn() as db:
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND state IN ('queued','running')", (pid,)).fetchone():
            raise ValueError('Dự án đang xử lý. Hãy đợi hoặc hủy tác vụ trước.')
        jid = uuid.uuid4().hex
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,0)', (jid, pid, kind, 'queued', 0, 'Đang chờ xử lý', '', time.time()))
    return jid


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
