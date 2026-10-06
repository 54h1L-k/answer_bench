"""Conservative baseline extraction with exact evidence and explicit abstentions.

This baseline is deliberately labeled unvalidated. It is not a semantic judge.
"""
from __future__ import annotations
import json
import re
import uuid
from .core import Invalid, dumps, now

VERSION = 'rules-v1-unvalidated'
LEVELS = ['none', 'mention', 'candidate', 'recommendation', 'strong_recommendation']

def matches(text, name):
    return list(re.finditer(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I))

def extract_text(text, entities):
    observations, known_names = [], set()
    ownership = {}
    for e in entities:
        for name in [e['name']] + e.get('aliases', []):
            ownership.setdefault(name.casefold(), set()).add(e['id'])
            known_names.add(name.casefold())
    for e in entities:
        evidence, levels, ambiguous = [], [], False
        full = matches(text, e['name'])
        names = [e['name']] if full else e.get('aliases', [])
        for name in names:
            for m in matches(text, name):
                ev = {'start': m.start(), 'end': m.end(), 'text': m.group()}
                evidence.append(ev)
                if len(ownership[name.casefold()]) > 1 or (len(name.split()) == 1 and len(name) < 8 and not full):
                    ambiguous = True
                    continue
                left = max(text.rfind('.', 0, m.start()), text.rfind('!', 0, m.start()), text.rfind('\n', 0, m.start()), text.rfind(';', 0, m.start())) + 1
                right_match = re.search(r'[.!\n;]', text[m.end():])
                right = m.end() + right_match.start() if right_match else len(text)
                prefix = text[left:m.start()].casefold()
                suffix = text[m.end():right].casefold()
                # Limit recommendation cues to the closest clause before the entity.
                prefix = re.split(r'\b(?:but|whereas)\b', prefix)[-1]
                neg = re.search(r"\b(?:avoid|skip|not recommend|don't recommend|do not recommend|cannot recommend)\b", prefix)
                neg = neg or re.match(r'[\s*,:-]*(?:is not recommended|is unsuitable|is closed)\b', suffix)
                if neg:
                    level = 'mention'
                    ev['negative'] = True
                elif re.search(r'\b(?:top pick|first choice|strongly recommend|best choice|my favorite)\b', prefix) or re.search(r'\b(?:my top pick|my first choice)\b', suffix):
                    level = 'strong_recommendation'
                elif re.search(r'\b(?:recommend|suggest|choose)\b', prefix) or re.match(r'[\s*,:-]*is recommended\b', suffix):
                    level = 'recommendation'
                elif re.search(r'\b(?:consider|option|candidate|could try)\b', prefix):
                    level = 'candidate'
                else:
                    level = 'mention'
                levels.append(level)
                ev['level'] = level
        contradictory = any(v.get('negative') for v in evidence) and any(LEVELS.index(l) >= 3 for l in levels)
        unknown = ambiguous or contradictory
        level = None if unknown else max(levels, key=LEVELS.index, default='none')
        observations.append({'entity_id': e['id'], 'level': level,
            'surfaced': None if ambiguous else bool(evidence),
            'recommended': None if unknown else LEVELS.index(level) >= 3,
            'strong': None if unknown else level == 'strong_recommendation',
            'rank': None, 'citation_present': None, 'evidence': evidence,
            'status': 'ambiguous' if ambiguous else 'conflicting' if contradictory else 'resolved'})
    candidates = []
    for m in re.finditer(r'\*\*([^*\n]{3,80})\*\*', text):
        if m.group(1).casefold() not in known_names:
            candidates.append({'name': m.group(1), 'start': m.start(1), 'end': m.end(1), 'status': 'unverified'})
    return observations, candidates

def extract_run(store, rid):
    run = store.run(rid)
    if run['status'] == 'running':
        raise Invalid('Wait for the runner to stop before extracting')
    c = run['manifest']['config']
    entities = [c['entity']] + c.get('cohort', [])
    batch = 'b_' + uuid.uuid4().hex[:12]
    turns = store.rows("SELECT t.*, e.run_id FROM turns t JOIN executions e ON e.id=t.execution_id WHERE e.run_id=? AND t.role='assistant' ORDER BY t.execution_id,t.sequence", (rid,))
    with store.transaction() as db:
        db.execute('INSERT INTO extraction_batches VALUES(?,?,?,?,?,?)', (batch, rid, VERSION, now(), 'complete', store.evidence_hash(rid)))
        for t in turns:
            obs, candidates = extract_text(t['content'], entities)
            for o in obs:
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?)', (batch, t['execution_id'], (t['sequence'] + 1) // 2, o['entity_id'], dumps(o)))
            for candidate in candidates:
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?)', (batch, t['execution_id'], (t['sequence'] + 1) // 2, candidate['name'], dumps(candidate)))
    return batch

def latest_batch(store, rid):
    return store.one("SELECT id FROM extraction_batches WHERE run_id=? AND status='complete' ORDER BY created_at DESC LIMIT 1", (rid,))['id']

def import_review(store, rid, records, source_batch=None):
    """Append corrected observations; preserve the original extraction batch."""
    source_batch = source_batch or latest_batch(store, rid)
    base = store.one('SELECT * FROM extraction_batches WHERE id=? AND run_id=?', (source_batch, rid))
    rows = store.rows('SELECT * FROM observations WHERE batch_id=?', (source_batch,))
    index = {(r['execution_id'], r['turn_index'], r['entity_id']): json.loads(r['data']) for r in rows}
    seen = set()
    for r in records:
        key = (r['execution_id'], r['turn_index'], r['entity_id'])
        if key not in index or key in seen or r.get('level') not in LEVELS or not r.get('reviewer'):
            raise Invalid('Invalid/duplicate review key, level or missing reviewer')
        seen.add(key)
        text = store.one('SELECT content FROM turns WHERE execution_id=? AND sequence=? AND role=?', (key[0], key[1] * 2 - 1, 'assistant'))['content']
        evidence = r.get('evidence', [])
        if r['level'] != 'none' and not evidence:
            raise Invalid('Positive review labels require evidence')
        for ev in evidence:
            if type(ev.get('start')) is not int or type(ev.get('end')) is not int or not (0 <= ev['start'] < ev['end'] <= len(text)) or text[ev['start']:ev['end']] != ev['text']:
                raise Invalid('Review evidence span does not match raw response')
        index[key] = index[key] | {'level': r['level'], 'status': 'reviewed', 'surfaced': r['level'] != 'none',
            'recommended': LEVELS.index(r['level']) >= 3, 'strong': r['level'] == 'strong_recommendation',
            'evidence': evidence, 'reviewer': r['reviewer']}
    bid = 'b_' + uuid.uuid4().hex[:12]
    with store.transaction() as db:
        db.execute('INSERT INTO extraction_batches VALUES(?,?,?,?,?,?)', (bid, rid, base['version'] + '+review', now(), 'complete', base['evidence_hash']))
        for key, val in index.items():
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?)', (bid, *key, dumps(val)))
        db.execute('INSERT INTO candidates SELECT ?,execution_id,turn_index,name,evidence FROM candidates WHERE batch_id=?', (bid, source_batch))
    return bid
