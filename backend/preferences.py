import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

from . import store
from .models import Settings


PREFERENCE_KEY = 'user-defaults'
LOCAL_FIELDS = {'title', 'hook_enabled', 'hook_start', 'hook_end', 'part_durations',
                'production_workflow', 'duration_min_ratio'}
REFERENCE_FIELD = 'voice_reference'
SHARED_FIELDS = set(Settings.model_fields) - LOCAL_FIELDS - {REFERENCE_FIELD}
REFERENCE_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.ogg', '.flac'}


def _directory():
    path = (store.DATA / '_preferences').resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _reference_path(name):
    if not name or Path(name).name != name:
        return None
    path = (_directory() / name).resolve()
    if not path.is_relative_to(_directory()) or path.suffix.lower() not in REFERENCE_EXTENSIONS:
        return None
    return path


def _normalized(settings):
    return Settings(**{key: value for key, value in settings.items() if key in Settings.model_fields}).model_dump()


def _shared(settings, has_reference=True):
    normalized = _normalized(settings)
    shared = {key: normalized[key] for key in Settings.model_fields if key in SHARED_FIELDS}
    if not has_reference:
        shared['voice_reference_text'] = ''
        shared['voice_reference_hash'] = ''
    return shared


def _row():
    with store.conn() as db:
        row = db.execute(
            'SELECT body, updated, source_project_id FROM preferences WHERE key=?',
            (PREFERENCE_KEY,),
        ).fetchone()
    if not row:
        return None
    body = json.loads(row['body'])
    body.update(updated=row['updated'], source_project_id=row['source_project_id'])
    return body


def _project_reference(project, strict=False):
    relative = project.get('settings', {}).get(REFERENCE_FIELD, '')
    if not relative:
        return None
    source = store.asset(project['id'], relative)
    if source.suffix.lower() not in REFERENCE_EXTENSIONS or not source.is_file():
        if strict:
            raise ValueError('Không tìm thấy file giọng mẫu của dự án.')
        return None
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    name = f'voice-reference-{digest}{source.suffix.lower()}'
    destination = _reference_path(name)
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return {'file': name, 'sha256': digest}


def _write(settings, reference, source_project_id):
    body = {'version': 1, 'settings': _shared(settings, bool(reference)), 'reference': reference}
    updated = time.time()
    with store.conn() as db:
        db.execute(
            'INSERT OR REPLACE INTO preferences (key, body, updated, source_project_id) VALUES (?,?,?,?)',
            (PREFERENCE_KEY, json.dumps(body, ensure_ascii=False), updated, source_project_id),
        )
    # Keep content-addressed reference assets: another project may still be
    # copying the previous reference, and cleanup must never fail a saved edit.
    body.update(updated=updated, source_project_id=source_project_id)
    return body


def initialize(fallback_settings=None):
    """Create the singleton preference row, migrating the latest project once."""
    with store.LOCK:
        with store.conn() as db:
            db.execute(
                'CREATE TABLE IF NOT EXISTS preferences '
                '(key TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL, source_project_id TEXT)'
            )
            existing = db.execute('SELECT 1 FROM preferences WHERE key=?', (PREFERENCE_KEY,)).fetchone()
            if existing:
                return _row()
            latest = db.execute('SELECT body FROM projects ORDER BY updated DESC LIMIT 1').fetchone()
        if latest:
            project = json.loads(latest['body'])
            reference = _project_reference(project, strict=False)
            return _write(project.get('settings', {}), reference, project.get('id'))
        defaults = fallback_settings or Settings(
            output_mode='single', narration_style='storytelling', opening_delay=0
        ).model_dump()
        return _write(defaults, None, None)


def current():
    preference = _row()
    if preference is None:
        preference = initialize()
    return preference


def save_project(project):
    """Atomically save an explicit user edit and promote its reusable fields."""
    with store.LOCK:
        reference = _project_reference(project, strict=True)
        body = {
            'version': 1,
            'settings': _shared(project['settings'], bool(reference)),
            'reference': reference,
        }
        updated = time.time()
        project['updated'] = updated
        project['revision'] = project.get('revision', 0) + 1
        with store.conn() as db:
            db.execute(
                'INSERT OR REPLACE INTO projects VALUES (?,?,?)',
                (project['id'], json.dumps(project, ensure_ascii=False), updated),
            )
            db.execute(
                'INSERT OR REPLACE INTO preferences (key, body, updated, source_project_id) VALUES (?,?,?,?)',
                (PREFERENCE_KEY, json.dumps(body, ensure_ascii=False), updated, project['id']),
            )
        return project


def _copy_reference(pid, reference):
    if not reference:
        return ''
    source = _reference_path(reference.get('file'))
    if not source or not source.is_file():
        raise ValueError('Giọng mẫu dùng chung không còn tồn tại. Hãy tải lại file giọng mẫu.')
    digest = reference.get('sha256', '')
    if not digest or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError('Giọng mẫu dùng chung bị thay đổi hoặc hỏng. Hãy tải lại file giọng mẫu.')
    relative = f'preference-reference-{digest[:16]}{source.suffix.lower()}'
    destination = store.asset(pid, relative)
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return relative


def settings_for_project(pid, base_settings):
    preference = current()
    result = _normalized(base_settings)
    for key, value in preference.get('settings', {}).items():
        if key in SHARED_FIELDS:
            result[key] = value
    result[REFERENCE_FIELD] = _copy_reference(pid, preference.get('reference'))
    return Settings(**result).model_dump()


def update_reference_transcript(fingerprint, text):
    """Update only transcript metadata, without promoting a worker's settings."""
    with store.LOCK, store.conn() as db:
        row = db.execute('SELECT body FROM preferences WHERE key=?', (PREFERENCE_KEY,)).fetchone()
        if not row:
            return
        body = json.loads(row['body'])
        reference = body.get('reference') or {}
        if reference.get('sha256') != fingerprint:
            return  # Another project/user already selected a different sample.
        body['settings'].update(voice_reference_text=text, voice_reference_hash=fingerprint)
        db.execute('UPDATE preferences SET body=?, updated=? WHERE key=?',
                   (json.dumps(body, ensure_ascii=False), time.time(), PREFERENCE_KEY))


def apply(project):
    """Apply defaults to an existing project, preserving source-specific fields."""
    updated_settings = settings_for_project(project['id'], project['settings'])
    changed = updated_settings != project['settings']
    if changed:
        project['settings'] = updated_settings
        project['exports'] = []
        project['preview_exports'] = []
    return project, changed


def status():
    preference = current()
    reference = preference.get('reference')
    path = _reference_path(reference.get('file')) if reference else None
    has_reference = bool(path and path.is_file())
    settings = dict(preference.get('settings', {}))
    settings[REFERENCE_FIELD] = has_reference
    return {
        'initialized': True,
        'source_project_id': preference.get('source_project_id'),
        'updated': preference.get('updated'),
        'has_voice_reference': has_reference,
        'settings': settings,
    }
