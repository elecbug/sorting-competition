from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, student_id TEXT UNIQUE NOT NULL, nickname TEXT NOT NULL,
 password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'student', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), problem_id INTEGER NOT NULL REFERENCES problems(id), source TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', created_at REAL NOT NULL, started_at REAL,
 finished_at REAL, profile TEXT, result TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS submission_queue ON submissions(status, id);
CREATE INDEX IF NOT EXISTS submission_user ON submissions(user_id, id DESC);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS problems (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
 is_open INTEGER NOT NULL DEFAULT 1, translations TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS testcases (
 id INTEGER PRIMARY KEY, problem_id INTEGER NOT NULL REFERENCES problems(id), title TEXT NOT NULL,
 input_csv TEXT NOT NULL, answer_csv TEXT NOT NULL, is_public INTEGER NOT NULL DEFAULT 1,
 row_count INTEGER NOT NULL, column_count INTEGER NOT NULL, digest TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_events (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 admin_id INTEGER NOT NULL REFERENCES users(id), old_student_id TEXT NOT NULL,
 new_student_id TEXT NOT NULL, created_at REAL NOT NULL
);
"""


class Database:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = data_dir / "competition.db"
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(problems)")}
            if "translations" not in columns:
                db.execute("ALTER TABLE problems ADD COLUMN translations TEXT NOT NULL DEFAULT '{}'")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('submissions_open', 'true')")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def rate_limit(self, key, limit, window):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM rate_limits WHERE expires_at<?", (now,))
            db.execute("INSERT INTO rate_limits VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET count=count+1", (key, now + window))
            count = db.execute("SELECT count FROM rate_limits WHERE key=?", (key,)).fetchone()[0]
        return count <= limit
