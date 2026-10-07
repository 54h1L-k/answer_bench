"""Crash-safe execution with bounded requests, isolated histories and manifests."""
from __future__ import annotations
import copy
import json
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from . import __version__
from .core import Invalid, digest, dumps, effective_engine, now
from .engines import EngineError, adapter
from .journeys import next_message, verify_bundle
from .storage import exclusive_run

def plan(config, bundle):
    verify_bundle(bundle)
    sessions = len(bundle['journeys']) * len(config['engines']) * config['sampling']['repetitions']
    responses = sum(j['max_turns'] for j in bundle['journeys']) * len(config['engines']) * config['sampling']['repetitions']
    return {'journeys': len(bundle['journeys']), 'sessions': sessions, 'response_ceiling': responses,
            'request_budget': config['execution']['max_requests'], 'retry_ceiling': responses * config['execution']['max_attempts'],
            'estimated_cost': 0 if all(e['adapter'] == 'fake' for e in config['engines']) else None,
            'note': 'Each retry consumes request budget. Live price estimates unavailable.'}

def create_run(store, config, bundle):
    verify_bundle(bundle)
    config = copy.deepcopy(config)
    config['engines'] = [effective_engine(e) for e in config['engines']]
    for e in config['engines']:
        adapter(e).check_credentials()
    rid = 'run_' + uuid.uuid4().hex[:12]
    manifest = {'run_id': rid, 'answerbench_version': __version__, 'schema_version': '0.1',
                'created_at': now(), 'config': config, 'config_hash': digest(config),
                'bundle_hash': bundle['hash'], 'plan': plan(config, bundle),
                'synthetic': all(e['adapter'] == 'fake' for e in config['engines']),
                'capabilities': {e['id']: adapter(e).capabilities() for e in config['engines']}}
    manifest['planned_hash'] = digest(manifest)
    with store.transaction() as db:
        db.execute('INSERT INTO runs VALUES(?,?,?,?,?)', (rid, now(), 'pending', dumps(manifest), dumps(bundle)))
        for kind, definitions in [('entity', [config['entity']] + config.get('cohort', [])), ('task', config['tasks']),
                                  ('profile', config['profiles']), ('context', config['contexts']), ('engine', config['engines']), ('journey', bundle['journeys'])]:
            db.executemany('INSERT INTO definitions VALUES(?,?,?,?)', [(rid, kind, d['id'], dumps(d)) for d in definitions])
        for j in bundle['journeys']:
            for e in config['engines']:
                for rep in range(config['sampling']['repetitions']):
                    xid = 'x_' + digest([rid, j['id'], e['id'], rep])[:20]
                    db.execute('INSERT INTO executions VALUES(?,?,?,?,?,?,?)', (xid, rid, j['id'], e['id'], rep, 'pending', None))
    return rid

def execute(store, rid, retry_failed=False, adapter_factory=adapter, sleep=time.sleep):
    with exclusive_run(store.path):
        run = store.run(rid)
        manifest, bundle = run['manifest'], run['bundle']
        verify_bundle(bundle)
        c = manifest['config']
        planned = {k: v for k, v in manifest.items() if k not in ('planned_hash', 'completed_at', 'outcomes', 'request_count', 'evidence_hash')}
        if digest(planned) != manifest['planned_hash'] or digest(c) != manifest['config_hash'] or bundle['hash'] != manifest['bundle_hash']:
            raise Invalid('Run manifest integrity failed')
        for e in c['engines']:
            adapter_factory(e).check_credentials()
        engine_map = {e['id']: e for e in c['engines']}
        journeys = {j['id']: j for j in bundle['journeys']}
        with store.transaction() as db:
            db.execute("UPDATE attempts SET status='interrupted', error='Process ended before response commit; remote billing unknown' WHERE status='started' AND execution_id IN (SELECT id FROM executions WHERE run_id=?)", (rid,))
            db.execute("UPDATE executions SET status='pending' WHERE run_id=? AND status IN ('running','budget_stopped')", (rid,))
            if retry_failed:
                db.execute("UPDATE executions SET status='pending' WHERE run_id=? AND status='failed'", (rid,))
            db.execute("UPDATE runs SET status='running' WHERE id=?", (rid,))
        jobs = store.rows("SELECT * FROM executions WHERE run_id=? AND status='pending' ORDER BY journey_id,engine_id,repetition", (rid,))
        random.Random(c['sampling']['seed']).shuffle(jobs)

        def status(xid, state, reason=None):
            with store.transaction() as db:
                db.execute('UPDATE executions SET status=?, stop_reason=? WHERE id=?', (state, reason, xid))

        def work(job):
            xid, j = job['id'], journeys[job['journey_id']]
            engine = adapter_factory(engine_map[job['engine_id']], c['execution']['timeout_seconds'])
            history = [{'role': t['role'], 'content': t['content']} for t in store.rows('SELECT role,content FROM turns WHERE execution_id=? ORDER BY sequence', (xid,))]
            status(xid, 'running')
            try:
                while True:
                    # A persisted user message may be awaiting a response after a crash.
                    if not history or history[-1]['role'] == 'assistant':
                        action = next_message(j, history)
                        if action is None:
                            status(xid, 'complete', 'policy_end')
                            return
                        message, branch = action
                        with store.transaction() as db:
                            db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)', (xid, len(history), 'user', message, branch, None))
                        history.append({'role': 'user', 'content': message})
                    turn = sum(t['role'] == 'user' for t in history)
                    while True:
                        with store.transaction() as db:
                            count = db.execute('SELECT count(*) FROM attempts WHERE execution_id IN (SELECT id FROM executions WHERE run_id=?)', (rid,)).fetchone()[0]
                            previous = db.execute('SELECT count(*) FROM attempts WHERE execution_id=? AND turn_index=?', (xid, turn)).fetchone()[0]
                            if previous >= c['execution']['max_attempts']:
                                status(xid, 'failed', 'attempt_limit')
                                return
                            if count >= c['execution']['max_requests']:
                                status(xid, 'budget_stopped', 'request_limit')
                                return
                            cur = db.execute('INSERT INTO attempts(execution_id,turn_index,attempt,started_at,status,request) VALUES(?,?,?,?,?,?)',
                                (xid, turn, previous + 1, now(), 'started', dumps(engine.request(history))))
                            aid = cur.lastrowid
                        try:
                            response = engine.send_message(history, f'{j["id"]}/{job["repetition"]}')
                        except EngineError as exc:
                            with store.transaction() as db:
                                db.execute('UPDATE attempts SET status=?, error=? WHERE id=?', ('error', str(exc), aid))
                            if exc.retryable and previous + 1 < c['execution']['max_attempts']:
                                sleep(min(2 ** previous, 8))
                                continue
                            status(xid, 'failed', str(exc))
                            return
                        with store.transaction() as db:
                            db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)', (xid, len(history), 'assistant', response.text, None, dumps(response.data())))
                            db.execute("UPDATE attempts SET status='complete' WHERE id=?", (aid,))
                        history.append({'role': 'assistant', 'content': response.text})
                        if response.status != 'completed':
                            status(xid, 'partial', 'provider_' + response.status)
                            return
                        break
            except Exception as exc:
                status(xid, 'failed', f'Local {type(exc).__name__}')
                raise
            finally:
                engine.close_session(history)

        with ThreadPoolExecutor(max_workers=c['execution']['concurrency']) as pool:
            list(pool.map(work, jobs))
        counts = store.rows('SELECT status,count(*) AS n FROM executions WHERE run_id=? GROUP BY status', (rid,))
        complete = all(x['status'] == 'complete' for x in counts)
        manifest['completed_at'] = now()
        manifest['outcomes'] = {x['status']: x['n'] for x in counts}
        manifest['request_count'] = store.one('SELECT count(*) AS n FROM attempts WHERE execution_id IN (SELECT id FROM executions WHERE run_id=?)', (rid,))['n']
        manifest['evidence_hash'] = store.evidence_hash(rid)
        with store.transaction() as db:
            db.execute('UPDATE runs SET status=?,manifest=? WHERE id=?', ('complete' if complete else 'incomplete', dumps(manifest), rid))
        return manifest
