"""Self-contained HTML with escaped evidence; no remote assets or telemetry."""
import html
import json
from pathlib import Path
from .core import write_json
from .metrics import report_data


def esc(value):
    return html.escape(str(value), quote=True)


def percent(value):
    return 'Unavailable' if value is None else f'{100 * value:.1f}%'


def metric(value):
    interval = value.get('ci95')
    uncertainty = f'{percent(interval[0])}–{percent(interval[1])}' if interval else 'Interval unavailable'
    return f'<b>{percent(value["rate"])}</b><small>{uncertainty} · {value["executions"]}/{value.get("planned", value["executions"])} evaluable executions · {value["journeys"]} journeys</small><small>{esc(", ".join(value["flags"]))}</small>'


def render_report(store, rid, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    report = report_data(store, rid)
    manifest = report['manifest']
    write_json(destination / 'report.json', report)
    write_json(destination / 'manifest.json', manifest)
    traces = [store.trace(x['id']) for x in store.rows('SELECT id FROM executions WHERE run_id=? ORDER BY engine_id,journey_id,repetition', (rid,))]
    write_json(destination / 'evidence.json', traces)
    engines = []
    for result in report['engines']:
        engine = result['engine']
        rows = ''.join(f'<tr><th>{esc(e["name"])}</th><td>{metric(e["surfaced"])}</td><td>{metric(e["recommended"])}</td><td>{metric(e["strong"])}</td></tr>' for e in result['entities'])
        slices = ''.join(f'<tr data-dimension="{esc(s["dimension"])}"><td>{esc(s["dimension"])}</td><th>{esc(s["value"])}</th><td>{metric(s)}</td></tr>' for s in result['slices'])
        options = ''.join(f'<option>{esc(d)}</option>' for d in sorted({s['dimension'] for s in result['slices']}))
        multi = ''.join(f'<div class="card"><span>{esc(k.replace("_", " "))}</span><h3>{percent(v["rate"])}</h3><small>{v["numerator"]}/{v["denominator"]} eligible observations</small></div>' for k,v in result['multi_turn'].items() if isinstance(v,dict) and 'rate' in v)
        stability = result['stability']
        tag = 'SYNTHETIC / OFFLINE' if engine['adapter'] == 'fake' else 'LIVE API'
        engines.append(f'''<section><div class="eyebrow">{tag}</div><h2>{esc(engine['id'])}</h2><p>{esc(engine.get('model', engine.get('preset', 'Deterministic fixture engine')))}</p>
        <div class="table"><table><thead><tr><th>Entity</th><th>Surfaced at least once</th><th>Recommended at least once</th><th>Strong recommendation</th></tr></thead><tbody>{rows}</tbody></table></div>
        <h3>Recommendation surface</h3><p>Target entity · weighted by the declared profile mix; no population prevalence claim.</p>
        <label>Explore <select onchange="filterSlices(this)"><option value="all">All dimensions</option>{options}</select></label>
        <div class="table"><table class="slices"><thead><tr><th>Dimension</th><th>Segment</th><th>Journey recommendation rate</th></tr></thead><tbody>{slices}</tbody></table></div>
        <h3>Across the conversation</h3><p>Unweighted descriptive metrics. Turns within a conversation are dependent.</p><div class="grid">{multi}</div>
        <p>First-discovery turn counts: {esc(json.dumps(result['multi_turn']['discovery_turn']))}</p>
        <h3>Fixed-cohort competition</h3><p>Target share of entity-execution recommendations: <b>{percent(result['share']['target_share'])}</b>. Repeated-set mean Jaccard: <b>{percent(stability['mean_jaccard'])}</b> across {stability['pairs']} nonempty pairs; {stability['both_empty']} both-empty pairs excluded.</p>
        <p>Counts: {esc(json.dumps(result['share']['counts']))}. These summaries are unweighted and limited to the configured cohort.</p></section>''')
    evidence = []
    for trace in traces:
        x = trace['execution']
        turns = ''.join(f'<div class="turn"><strong>{esc(t["role"].upper())}</strong><pre>{esc(t["content"])}</pre></div>' for t in trace['turns'])
        observations = store.rows('SELECT turn_index,entity_id,data FROM observations WHERE batch_id=? AND execution_id=? ORDER BY turn_index,entity_id', (report['batch']['id'], x['id']))
        labels = ''.join(f'<tr><td>{r["turn_index"]}</td><td>{esc(r["entity_id"])}</td><td>{esc(json.loads(r["data"])["level"])}</td><td>{esc(json.loads(r["data"])["status"])}</td></tr>' for r in observations)
        evidence.append(f'<details><summary>{esc(x["engine_id"])} · {esc(x["journey_id"])} · repeat {x["repetition"]+1} · {esc(x["status"])}</summary><p>Execution: {esc(x["id"])} · stop: {esc(x["stop_reason"])}</p>{turns}<table><tr><th>Turn</th><th>Entity</th><th>Label</th><th>Status</th></tr>{labels}</table></details>')
    candidates = store.rows('SELECT name,count(*) AS n FROM candidates WHERE batch_id=? GROUP BY name ORDER BY n DESC,name', (report['batch']['id'],))
    candidate_html = '<ul>' + ''.join(f'<li>{esc(c["name"])} — {c["n"]} occurrences; unverified</li>' for c in candidates) + '</ul>' if candidates else '<p>No additional bold-name candidates detected.</p>'
    disclaimer = 'Synthetic demonstration. All answers are generated locally from fictional fixtures; these are not market measurements.' if manifest['synthetic'] else 'API measurement. Check each engine label: this run may include synthetic fixtures. Results do not represent consumer chat products.'
    source = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AnswerBench · evidence report</title><style>
    :root{{--ink:#172b33;--sage:#dce9df;--paper:#f6f5ef;--muted:#52656a}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}}main{{max-width:1200px;margin:auto;padding:50px 28px}}header{{background:var(--ink);color:white;padding:48px;border-radius:18px}}h1{{font-size:54px;line-height:1.05;letter-spacing:-2px;margin:16px 0}}h2{{font-size:32px;margin:8px 0}}h3{{margin:28px 0 10px}}p{{max-width:1000px}}.eyebrow{{font-size:12px;letter-spacing:2px;font-weight:700}}.banner{{background:var(--sage);padding:22px 28px;border-radius:12px;margin:24px 0}}section{{margin:36px 0;padding:30px;background:#fff;border:1px solid #dbe0da;border-radius:14px}}small{{display:block;font-size:12px;color:var(--muted)}}table{{width:100%;border-collapse:collapse;text-align:left;font-size:14px}}td,th{{padding:15px 12px;vertical-align:top;border-bottom:1px solid #e2e7e2}}thead th{{background:#eef2ed;font-size:12px}}td b{{font-size:22px}}.table{{overflow:auto}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}.card{{padding:18px;background:var(--paper);border-radius:10px}}.card span{{text-transform:capitalize;font-size:14px}}.card h3{{margin:8px 0;font-size:26px}}select{{padding:8px;margin:8px}}details{{border-bottom:1px solid #dde3dd;padding:14px 0}}summary{{cursor:pointer;font-size:14px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 inherit}}.turn{{background:var(--paper);padding:14px;margin:12px 0;border-radius:8px}}a{{color:#126951}}code{{overflow-wrap:anywhere}}@media(max-width:700px){{main{{padding:18px}}header,section{{padding:22px}}h1{{font-size:38px}}.grid{{grid-template-columns:1fr 1fr}}}}@media print{{details{{display:none}}section{{break-inside:avoid}}}}
    </style></head><body><main><header><div class="eyebrow">ANSWERBENCH / EVIDENCE REPORT / ALPHA</div><h1>Who gets recommended,<br>for whom, and when?</h1><p>{esc(manifest['config']['project']['name'])}</p><small style="color:#b7cdc2">{esc(rid)} · {esc(report['status'])} · {esc(manifest['created_at'])}</small></header>
    <div class="banner"><strong>{disclaimer}</strong><br>Extraction: {esc(report['batch']['version'])}. Rates count complete, resolved journeys only; unknown and failed responses are excluded and disclosed.</div>
    <section><div class="eyebrow">READ THIS FIRST</div><h2>A measured sample, with its limits</h2><p>{manifest['plan']['journeys']} frozen journeys · {manifest['plan']['sessions']} planned executions · {manifest.get('request_count',0)} attempted requests.</p><ul>{''.join('<li>'+esc(t)+'</li>' for t in report['limitations'])}</ul><p>95% intervals resample journeys within task × profile × context strata, keeping repeats together. Missing strata suppress estimates; sparse or degenerate samples suppress intervals. Refusals are valid negative answers. This demo's intervals describe fixture variation only.</p></section>
    {''.join(engines)}<section><h2>Discovery queue</h2><p>Possible names from bold text, awaiting entity review. They are never automatically added to the comparison cohort.</p>{candidate_html}</section>
    <section><h2>Evidence browser</h2><p>Expand an execution to inspect the exact conversation and extracted labels. Exact offsets and provider payloads are available in the JSON exports.</p>{''.join(evidence)}</section>
    <section><h2>Reproduce and audit</h2><p><a href="report.json">Metrics JSON</a> · <a href="manifest.json">Run manifest</a> · <a href="evidence.json">Raw evidence</a></p><p>Bundle SHA-256: <code>{esc(manifest['bundle_hash'])}</code></p><p>Evidence SHA-256: <code>{esc(manifest.get('evidence_hash','pending'))}</code></p></section><footer>AnswerBench · open measurement, explicit assumptions · v0.1 alpha</footer></main>
    <script>function filterSlices(select){{const rows=select.closest('section').querySelectorAll('tr[data-dimension]');for(const row of rows)row.hidden=select.value!=='all'&&row.dataset.dimension!==select.value;}}</script></body></html>'''
    (destination / 'index.html').write_text(source)
    return str((destination / 'index.html').resolve())
