"""Tiny SQL layer: SQLite for local/dev, PostgreSQL (Azure Database for PostgreSQL) when DATABASE_URL is set."""
import os, sqlite3, threading

URL = os.environ.get('DATABASE_URL', '').replace('postgresql+psycopg2://', 'postgresql://').replace('postgres://', 'postgresql://')
PG = URL.startswith('postgresql://')
_here = os.path.dirname(os.path.abspath(__file__))
SQLITE = os.environ.get('SQLITE_PATH') or (('/home/data/hub.db') if os.environ.get('WEBSITE_SITE_NAME') else os.path.join(_here, 'hub.db'))
_pool = None
_lock = threading.Lock()

def _get_pool():
    global _pool
    with _lock:
        if _pool is None:
            import psycopg2.pool
            _pool = psycopg2.pool.ThreadedConnectionPool(1, int(os.environ.get('DB_POOL', 10)), URL)
    return _pool

class Conn:
    def __init__(self):
        if PG:
            import psycopg2.extras
            self.raw = _get_pool().getconn()
            self._cf = psycopg2.extras.RealDictCursor
        else:
            os.makedirs(os.path.dirname(SQLITE), exist_ok=True)
            self.raw = sqlite3.connect(SQLITE, timeout=30)
            self.raw.row_factory = sqlite3.Row
            self.raw.execute('PRAGMA journal_mode=WAL')
    def x(self, sql, a=()):
        c = self.raw.cursor(cursor_factory=self._cf) if PG else self.raw.cursor()
        c.execute(sql.replace('?', '%s') if PG else sql, tuple(a))
        return c
    def q(self, sql, a=()):
        return [dict(r) for r in self.x(sql, a).fetchall()]
    def one(self, sql, a=()):
        r = self.x(sql, a).fetchone()
        return dict(r) if r else None
    def ins(self, table, **v):
        cols = ','.join(v)
        c = self.x(f'INSERT INTO {table}({cols}) VALUES({",".join("?" * len(v))}) RETURNING id', list(v.values()))
        return c.fetchone()['id']
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self):
        if PG:
            try: self.raw.rollback()
            finally: _get_pool().putconn(self.raw)
        else: self.raw.close()
    def __enter__(self): return self
    def __exit__(self, et, ev, tb):
        (self.rollback if et else self.commit)(); self.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id {PK}, username TEXT UNIQUE NOT NULL, name TEXT, title TEXT DEFAULT '', pw_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0, block_reason TEXT DEFAULT '', break_until BIGINT DEFAULT 0, violations INTEGER DEFAULT 0, online INTEGER DEFAULT 0, note TEXT DEFAULT '', since BIGINT DEFAULT 0, last_seen BIGINT DEFAULT 0, discord_id TEXT DEFAULT '', github_login TEXT DEFAULT '', default_pw INTEGER DEFAULT 0, created BIGINT, theme TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS ugroups(id {PK}, name TEXT UNIQUE NOT NULL, description TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER, user_id INTEGER, PRIMARY KEY(group_id, user_id));
CREATE TABLE IF NOT EXISTS tasks(id {PK}, title TEXT, description TEXT DEFAULT '', status TEXT DEFAULT 'todo', priority TEXT DEFAULT 'Medium', due TEXT DEFAULT '', assignee_id INTEGER, group_id INTEGER, idea_id INTEGER, created_by INTEGER, created BIGINT, completed_at BIGINT, completed_by INTEGER);
CREATE TABLE IF NOT EXISTS logs(id {PK}, user_id INTEGER, task_id INTEGER, text TEXT, hours REAL DEFAULT 0, ts BIGINT);
CREATE TABLE IF NOT EXISTS ideas(id {PK}, title TEXT, body TEXT DEFAULT '', author_id INTEGER, status TEXT DEFAULT 'open', created BIGINT, decided_by INTEGER);
CREATE TABLE IF NOT EXISTS votes(idea_id INTEGER, user_id INTEGER, value INTEGER, PRIMARY KEY(idea_id, user_id));
CREATE TABLE IF NOT EXISTS comments(id {PK}, idea_id INTEGER, user_id INTEGER, text TEXT, ts BIGINT);
CREATE TABLE IF NOT EXISTS violations(id {PK}, user_id INTEGER, ts BIGINT, place TEXT, matched TEXT, action TEXT);
CREATE TABLE IF NOT EXISTS bans(id {PK}, user_id INTEGER, starts BIGINT, ends BIGINT, reason TEXT DEFAULT '', by_id INTEGER, auto INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS presence(id {PK}, user_id INTEGER, started BIGINT, ended BIGINT, note TEXT DEFAULT '', task_id INTEGER);
CREATE TABLE IF NOT EXISTS activity(id {PK}, ts BIGINT, source TEXT, actor TEXT, user_id INTEGER, kind TEXT, summary TEXT, place TEXT DEFAULT '', url TEXT DEFAULT '', synced INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meetings(id {PK}, channel_id TEXT, channel TEXT, started BIGINT, ended BIGINT, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS attendance(id {PK}, meeting_id INTEGER, discord_id TEXT, name TEXT, user_id INTEGER, joined BIGINT, left_at BIGINT);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_tasks_assignee ON tasks(assignee_id);
CREATE INDEX IF NOT EXISTS ix_tasks_group ON tasks(group_id);
CREATE INDEX IF NOT EXISTS ix_activity_ts ON activity(ts);
CREATE INDEX IF NOT EXISTS ix_bans_user ON bans(user_id);
"""

def init_db():
    pk = 'SERIAL PRIMARY KEY' if PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    with Conn() as c:
        for stmt in SCHEMA.replace('{PK}', pk).split(';'):
            if stmt.strip(): c.x(stmt)
        if PG: c.x("ALTER TABLE users ADD COLUMN IF NOT EXISTS theme TEXT DEFAULT ''")
        elif 'theme' not in [r['name'] for r in c.q('PRAGMA table_info(users)')]: c.x("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT ''")
