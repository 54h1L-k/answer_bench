"""Journey-level estimands with stratified cluster resampling."""
from __future__ import annotations
import itertools
import json
import math
import random
from collections import defaultdict
from statistics import mean
from .core import Invalid, Incompatible, digest, dumps, now
from .extraction import latest_batch

METRICS_VERSION = 'journey-metrics-v1'

def percentile(values, q):
    values = sorted(values)
    at = (len(values) - 1) * q
    lo, hi = math.floor(at), math.ceil(at)
    return values[lo] + (values[hi] - values[lo]) * (at - lo)

def estimate(samples, expected, weights, settings):
    """samples: {journey_id: [replicate values]}; expected: {journey_id: stratum}."""
    by_stratum = defaultdict(list)
    for jid, vals in samples.items():
        if vals:
            by_stratum[expected[jid]].append(mean(vals))
    strata = sorted(set(expected.values()))
    n = sum(len(v) for v in by_stratum.values())
    result = {'rate': None, 'ci95': None, 'journeys': n, 'executions': sum(map(len, samples.values())), 'flags': []}
    if any(not by_stratum[s] for s in strata):
        result['flags'].append('missing_stratum')
        return result
    norm = sum(weights[s] for s in strata)
    calc = lambda groups: sum(weights[s] * mean(groups[s]) for s in strata) / norm
    result['rate'] = calc(by_stratum)
    if n < settings['min_journeys'] or any(len(by_stratum[s]) < 2 for s in strata):
        result['flags'].append('sparse')
        return result
    rng = random.Random(settings['seed'])
    # Stable iteration order is essential for reproducible bootstrap results.
    strata = sorted(strata)
    values = [calc({s: rng.choices(by_stratum[s], k=len(by_stratum[s])) for s in strata}) for _ in range(settings['draws'])]
    result['ci95'] = [percentile(values, .025), percentile(values, .975)]
    if result['ci95'][0] == result['ci95'][1]:
        result['flags'].append('degenerate_interval')
        result['ci95'] = None
    return result

def distance_band(target, context):
    if not all(k in loc for loc in (target, context) for k in ('lat', 'lon')):
        return 'unknown'
    a, b = math.radians(target['lat']), math.radians(context['lat'])
    dlat, dlon = b - a, math.radians(context['lon'] - target['lon'])
    v = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    km = 6371.0088 * 2 * math.asin(min(1, math.sqrt(v)))
    for lo, hi in [(0, 1), (1, 2), (2, 5), (5, 10)]:
        if lo <= km < hi:
            return f'{lo}–{hi} km'
    return '≥10 km'

def data(store, rid, batch=None):
    run = store.run(rid)
    batch = batch or latest_batch(store, rid)
    b = store.one('SELECT * FROM extraction_batches WHERE id=? AND run_id=?', (batch, rid))
    if run['status'] == 'running' or b['evidence_hash'] != store.evidence_hash(rid):
        raise Invalid('Evidence changed since extraction; stop the runner and extract again')
    js = {j['id']: j for j in run['bundle']['journeys']}
    obs = defaultdict(lambda: defaultdict(dict))
    for r in store.rows('SELECT * FROM observations WHERE batch_id=? ORDER BY turn_index', (batch,)):
        obs[r['execution_id']][r['entity_id']][r['turn_index']] = json.loads(r['data'])
    xs = store.rows('SELECT * FROM executions WHERE run_id=? ORDER BY journey_id,engine_id,repetition', (rid,))
    return run, b, js, xs, obs

def aggregate(xs, js, obs, eid, settings, field='recommended'):
    expected = {j: js[j]['stratum'] for j in sorted({x['journey_id'] for x in xs})}
    weights = {j['stratum']: j['weight'] for j in js.values()}
    samples = defaultdict(list)
    missing = 0
    for x in xs:
        turns = obs[x['id']].get(eid, {})
        valid = x['status'] == 'complete' and len(turns) == js[x['journey_id']]['max_turns']
        vals = [o.get(field) for o in turns.values()]
        if valid and vals and all(v is not None for v in vals):
            samples[x['journey_id']].append(int(any(vals)))
        else:
            missing += 1
    out = estimate(samples, expected, weights, settings)
    out.update(planned=len(xs), missing=missing, completed=sum(x['status'] == 'complete' for x in xs))
    if missing:
        out['flags'].append('complete_case_estimate')
    return out

def report_data(store, rid, batch=None):
    run, b, js, xs, obs = data(store, rid, batch)
    c = run['manifest']['config']
    entities = [c['entity']] + c.get('cohort', [])
    target = c['entity']['id']
    engines = []
    for engine in c['engines']:
        ex = [x for x in xs if x['engine_id'] == engine['id']]
        result = {'engine': engine, 'entities': [], 'slices': [], 'multi_turn': {}, 'stability': {}}
        for e in entities:
            result['entities'].append({'id': e['id'], 'name': e['name'], **{field: aggregate(ex, js, obs, e['id'], c['analysis'], field) for field in ('surfaced', 'recommended', 'strong')}})
        dimensions = {'planning_style': lambda j: j['profile']['planning_style'], 'interaction_style': lambda j: j['profile']['interaction_style'],
                      'specificity': lambda j: j['profile']['specificity'], 'task': lambda j: j['task']['id'], 'mode': lambda j: j['mode'],
                      'location': lambda j: j['context']['location']['label'],
                      'budget': lambda j: str(j['context']['budget']['amount']) + ' ' + j['context']['budget']['currency'],
                      'distance': lambda j: distance_band(c['entity'].get('location', {}), j['context']['location'])}
        for dim, getter in dimensions.items():
            for val in sorted({getter(js[x['journey_id']]) for x in ex}):
                subset = [x for x in ex if getter(js[x['journey_id']]) == val]
                result['slices'].append({'dimension': dim, 'value': val, **aggregate(subset, js, obs, target, c['analysis'])})
        discovery, late_risk, late, retained, retention_risk, transitions, survive = defaultdict(int), 0, 0, 0, 0, [], []
        sets = defaultdict(list)
        reco_counts = defaultdict(int)
        for x in ex:
            turns = obs[x['id']].get(target, {})
            if x['status'] != 'complete' or len(turns) != js[x['journey_id']]['max_turns'] or any(o['recommended'] is None or o['surfaced'] is None for o in turns.values()):
                continue
            ordered = [turns[k] for k in sorted(turns)]
            first = next((i+1 for i, o in enumerate(ordered) if o['surfaced']), None)
            if len(ordered) > 1:
                discovery[str(first) if first else 'never'] += 1
                if not ordered[0]['surfaced']:
                    late_risk += 1
                    late += int(first is not None)
                if ordered[0]['recommended']:
                    retention_risk += 1
                    retained += int(ordered[-1]['recommended'])
                    survive.append(int(all(o['recommended'] for o in ordered)))
                for prev, nxt in zip(ordered, ordered[1:]):
                    if prev['recommended']:
                        transitions.append(int(nxt['recommended']))
            # Fixed-cohort set metrics exclude unknown/missing entity observations.
            if any(len(obs[x['id']].get(e['id'], {})) != len(ordered) or any(o['recommended'] is None for o in obs[x['id']].get(e['id'], {}).values()) for e in entities):
                continue
            recs = {e['id'] for e in entities if any(o['recommended'] for o in obs[x['id']][e['id']].values())}
            sets[x['journey_id']].append(recs)
            for eid in recs:
                reco_counts[eid] += 1
        overlaps, empty = [], 0
        for group in sets.values():
            for a, z in itertools.combinations(group, 2):
                if not a | z:
                    empty += 1
                else:
                    overlaps.append(len(a & z) / len(a | z))
        result['stability'] = {'mean_jaccard': mean(overlaps) if overlaps else None, 'pairs': len(overlaps), 'both_empty': empty}
        result['share'] = {'cohort': [e['id'] for e in entities], 'counts': dict(reco_counts),
                           'target_share': reco_counts[target] / sum(reco_counts.values()) if sum(reco_counts.values()) else None,
                           'weighting': 'unweighted descriptive entity-execution share'}
        ratio = lambda num, den: {'numerator': num, 'denominator': den, 'rate': num / den if den else None}
        result['multi_turn'] = {'discovery_turn': dict(discovery), 'late_discovery': ratio(late, late_risk),
            'final_retention': ratio(retained, retention_risk), 'persistence': ratio(sum(transitions), len(transitions)),
            'survival_all_refinements': ratio(sum(survive), len(survive)), 'weighting': 'unweighted descriptive; no independence claim across turns'}
        engines.append(result)
    return {'run_id': rid, 'status': run['status'], 'manifest': run['manifest'], 'batch': b,
            'metric_version': METRICS_VERSION, 'engines': engines,
            'candidate_count': store.one('SELECT count(*) AS n FROM candidates WHERE batch_id=?', (b['id'],))['n'],
            'limitations': ['Rules extractor is unvalidated; inspect evidence or import human reviews.',
                'Intervals reflect sampled journeys, not simulator validity or provider drift.',
                'API surfaces may differ from consumer products. Missing trials are not negative answers.',
                'Rank and entity citation attribution are unavailable in this baseline.',
                'Share, stability and multi-turn summaries are unweighted descriptive statistics.']}

def compare(store, a_id, z_id, engine_a=None, engine_b=None):
    a, ab, aj, ax, ao = data(store, a_id)
    z, zb, zj, zx, zo = data(store, z_id)
    ac, zc = a['manifest']['config'], z['manifest']['config']
    ea = engine_a or ac['engines'][0]['id']
    ez = engine_b or zc['engines'][0]['id']
    ae = next((e for e in ac['engines'] if e['id'] == ea), None)
    ze = next((e for e in zc['engines'] if e['id'] == ez), None)
    if not ae or not ze:
        raise Incompatible('Unknown engine ID')
    changed = []
    if {k:v for k,v in ae.items() if k != 'id'} != {k:v for k,v in ze.items() if k != 'id'}:
        changed.append('engine_conditions')
        if not engine_a or not engine_b:
            raise Incompatible('Engine conditions differ; explicitly select --engine-a and --engine-b for an engine contrast')
    checks = [('journey bundle', a['bundle']['hash'], z['bundle']['hash']), ('entity cohort', [ac['entity']] + ac.get('cohort', []), [zc['entity']] + zc.get('cohort', [])),
              ('repetitions', ac['sampling']['repetitions'], zc['sampling']['repetitions']), ('extractor', ab['version'], zb['version']), ('analysis', ac['analysis'], zc['analysis'])]
    for label, left, right in checks:
        if left != right:
            raise Incompatible(f'Incompatible {label}')
    target = ac['entity']['id']
    def valid_values(execs, observations, engine):
        out = {}
        for x in execs:
            ts = observations[x['id']].get(target, {})
            if x['engine_id'] == engine and x['status'] == 'complete' and len(ts) == aj[x['journey_id']]['max_turns'] and all(o['recommended'] is not None for o in ts.values()):
                out[(x['journey_id'], x['repetition'])] = int(any(o['recommended'] for o in ts.values()))
        return out
    av, zv = valid_values(ax, ao, ea), valid_values(zx, zo, ez)
    paired = sorted(av.keys() & zv.keys())
    samples = defaultdict(list)
    for key in paired:
        samples[key[0]].append(zv[key] - av[key])
    delta = estimate(samples, {jid:j['stratum'] for jid,j in aj.items()}, {j['stratum']:j['weight'] for j in aj.values()}, ac['analysis'])
    paired_a, paired_z = defaultdict(list), defaultdict(list)
    for key in paired:
        paired_a[key[0]].append(av[key])
        paired_z[key[0]].append(zv[key])
    expected = {jid: j['stratum'] for jid, j in aj.items()}
    weights = {j['stratum']: j['weight'] for j in aj.values()}
    result = {'paired_baseline': estimate(paired_a, expected, weights, ac['analysis']),
              'paired_comparison': estimate(paired_z, expected, weights, ac['analysis']),
              'baseline': a_id, 'comparison': z_id, 'engine_a': ea, 'engine_b': ez, 'changed_factors': changed,
              'matched_executions': len(paired), 'excluded_pairs': len(aj) * ac['sampling']['repetitions'] - len(paired),
              'delta_recommendation_rate': delta, 'note': 'Paired journey-cluster difference; observed association, not a causal estimate.',
              'extraction_batches': [ab['id'], zb['id']], 'metric_version': METRICS_VERSION}
    with store.transaction() as db:
        db.execute('INSERT OR IGNORE INTO comparisons VALUES(?,?,?,?,?)', ('cmp_'+digest(result)[:16], now(), a_id, z_id, dumps(result)))
    return result
