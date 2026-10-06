"""Append-only evidence, foreign keys and per-turn transactions."""
from __future__ import annotations
import contextlib
import json
import sqlite3
import threading
from pathlib import Path
from .core import Invalid, dumps, digest

SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, manifest TEXT NOT NULL, bundle TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS definitions(run_id TEXT REFERENCES runs(id), kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(run_id,kind,id));
CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), journey_id TEXT NOT NULL, engine_id TEXT NOT NULL, repetition INTEGER NOT NULL, status TEXT NOT NULL, stop_reason TEXT, UNIQUE(run_id,journey_id,engine_id,repetition));
CREATE TABLE IF NOT EXISTS turns(execution_id TEXT REFERENCES executions(id), sequence INTEGER, role TEXT NOT NULL, content TEXT NOT NULL, branch TEXT, response TEXT, PRIMARY KEY(execution_id,sequence));
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, execution_id TEXT REFERENCES executions(id), turn_index INTEGER, attempt INTEGER, started_at TEXT, status TEXT, request TEXT, error TEXT, UNIQUE(execution_id,turn_index,attempt));
CREATE TABLE IF NOT EXISTS extraction_batches(id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), version TEXT, created_at TEXT, status TEXT, evidence_hash TEXT);
CREATE TABLE IF NOT EXISTS observations(batch_id TEXT REFERENCES extraction_batches(id), execution_id TEXT REFERENCES executions(id), turn_index INTEGER, entity_id TEXT, data TEXT NOT NULL, PRIMARY KEY(batch_id,execution_id,turn_index,entity_id));
CREATE TABLE IF NOT EXISTS candidates(batch_id TEXT REFERENCES extraction_batches(id), execution_id TEXT REFERENCES executions(id), turn_index INTEGER, name TEXT, evidence TEXT);
CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY, created_at TEXT, baseline_run TEXT REFERENCES runs(id), comparison_run TEXT REFERENCES runs(id), data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS executions_run ON executions(run_id,status);
CREATE INDEX IF NOT EXISTS observations_entity ON observations(batch_id,entity_id);
'''

class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise Invalid(f'Unsupported database schema {version}')
        self.db.executescript(SCHEMA)
        self.db.execute('PRAGMA user_version=1')
        self.db.commit()

    @contextlib.contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        if not rows:
            raise Invalid('Requested record does not exist')
        return rows[0]

    def run(self, rid):
        row = self.one('SELECT * FROM runs WHERE id=?', (rid,))
        row['manifest'] = json.loads(row['manifest'])
        row['bundle'] = json.loads(row['bundle'])
        return row

    def evidence_hash(self, rid):
        turns = self.rows('SELECT t.* FROM turns t JOIN executions e ON e.id=t.execution_id WHERE e.run_id=? ORDER BY t.execution_id,t.sequence', (rid,))
        executions = self.rows('SELECT id,status,stop_reason FROM executions WHERE run_id=? ORDER BY id', (rid,))
        return digest({'turns': turns, 'executions': executions})

    def latest(self):
        return self.one('SELECT id FROM runs ORDER BY created_at DESC LIMIT 1')['id']

    def trace(self, xid):
        execution = self.one('SELECT * FROM executions WHERE id=?', (xid,))
        turns = self.rows('SELECT * FROM turns WHERE execution_id=? ORDER BY sequence', (xid,))
        for t in turns:
            if t['response']:
                t['response'] = json.loads(t['response'])
        return {'execution': execution, 'turns': turns,
                'attempts': self.rows('SELECT * FROM attempts WHERE execution_id=? ORDER BY id', (xid,))}

    def close(self):
        self.db.close()

@contextlib.contextmanager
def exclusive_run(path):
    # Advisory OS lock releases on process death. No stale lock recovery guesswork.
    import fcntl
    lockpath = str(path) + '.runner.lock'
    with open(lockpath, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Invalid('Another runner owns this database') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
