"""Bounded project journals: progress, measured repairs and redacted tracebacks."""
import re
import time

MAX_EVENTS = 4000


def safe(message):
    text = str(message)
    text = re.sub(r'(?i)(authorization[\s"\x27:]*bearer\s+)[^\s"\x27]+', r'\1[KEY]', text)
    text = re.sub(r'sk-[A-Za-z0-9_-]+|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]+', '[KEY]', text)
    return text[-12000:]


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS job_logs (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
        job_id TEXT NOT NULL, created REAL NOT NULL, level TEXT NOT NULL,
        message TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS job_logs_project ON job_logs(project_id, seq)')


def append(db, pid, jid, level, message):
    message = safe(message)
    last = db.execute('SELECT level,message FROM job_logs WHERE job_id=? ORDER BY seq DESC LIMIT 1', (jid,)).fetchone()
    if last and last['level'] == level and last['message'] == message:
        return
    db.execute('INSERT INTO job_logs(project_id,job_id,created,level,message) VALUES(?,?,?,?,?)',
               (pid, jid, time.time(), level, message))
    db.execute('DELETE FROM job_logs WHERE project_id=? AND seq < COALESCE('
               '(SELECT seq FROM job_logs WHERE project_id=? ORDER BY seq DESC LIMIT 1 OFFSET ?),0)',
               (pid, pid, MAX_EVENTS - 1))


def read(db, pid, after=None, limit=200):
    if after is None:
        rows = db.execute('SELECT * FROM job_logs WHERE project_id=? ORDER BY seq DESC LIMIT ?', (pid, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]
    return [dict(r) for r in db.execute('SELECT * FROM job_logs WHERE project_id=? AND seq>? ORDER BY seq LIMIT ?',
                                       (pid, after, limit))]
