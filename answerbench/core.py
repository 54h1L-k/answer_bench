"""Canonical serialization, strict configuration and typed result contracts."""
from __future__ import annotations
import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import yaml

class Invalid(ValueError):
    pass

class Incompatible(Invalid):
    pass

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()

def now():
    return datetime.now(timezone.utc).isoformat()

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)

def keys(obj, allowed, where):
    if not isinstance(obj, dict):
        raise Invalid(f"{where} must be a mapping")
    unexpected = set(obj) - set(allowed.split())
    if unexpected:
        raise Invalid(f"Unknown {where} fields: {', '.join(sorted(unexpected))}")

def positive(value, name, upper=100000):
    if type(value) is not int or not 1 <= value <= upper:
        raise Invalid(f"{name} must be an integer in 1..{upper}")

def string(value, where):
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{where} must be a nonempty string")

def records(items, name):
    if not isinstance(items, list) or not items:
        raise Invalid(f"{name} must be a nonempty list")
    ids = []
    for item in items:
        if not isinstance(item, dict):
            raise Invalid(f"{name} items must be mappings")
        string(item.get('id'), f'{name}.id')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', item['id']):
            raise Invalid(f"Unsafe ID in {name}: use letters, digits, hyphens or underscores")
        ids.append(item['id'])
    if len(set(ids)) != len(ids):
        raise Invalid(f"Duplicate IDs in {name}")

DIMS = {
    'planning_style': ['holistic', 'atomic'],
    'interaction_style': ['direct', 'conversational'],
    'constraint_level': ['low', 'medium', 'high'],
    'familiarity': ['novice', 'familiar', 'expert'],
    'decision_style': ['exploratory', 'comparison', 'decisive'],
    'price_sensitivity': ['low', 'medium', 'high'],
    'specificity': ['broad', 'moderate', 'specific'],
}

def validate(c):
    keys(c, 'schema_version project entity cohort tasks profiles contexts journeys sampling engines execution analysis', 'config')
    if c.get('schema_version') != '0.1':
        raise Invalid('schema_version must be "0.1"')
    keys(c.get('project'), 'name database', 'project')
    for k in ('name', 'database'):
        string(c['project'].get(k), 'project.' + k)
    if not isinstance(c.get('cohort', []), list):
        raise Invalid('cohort must be a list')
    entities = [c.get('entity')] + c.get('cohort', [])
    records(entities, 'entities')
    for e in entities:
        keys(e, 'id name type aliases categories location website attributes market', 'entity')
        string(e.get('name'), 'entity.name')
        string(e.get('type'), 'entity.type')
        if not isinstance(e.get('aliases', []), list) or any(not isinstance(a, str) or not a.strip() for a in e.get('aliases', [])):
            raise Invalid('aliases must be nonempty strings')
    for name in ('tasks', 'profiles', 'contexts', 'engines'):
        records(c.get(name), name)
    for t in c['tasks']:
        keys(t, 'id goal intent category constraints allow_entity_names', 'task')
        string(t.get('goal'), 'task.goal')
        string(t.get('category'), 'task.category')
        if t.get('intent') not in ('discovery', 'planning', 'comparison', 'alternative', 'capability'):
            raise Invalid('Unsupported task intent')
        if not isinstance(t.get('constraints', {}), dict):
            raise Invalid('task.constraints must be a mapping')
    for p in c['profiles']:
        keys(p, 'id weight ' + ' '.join(DIMS), 'profile')
        for k, options in DIMS.items():
            if p.get(k) not in options:
                raise Invalid(f'{p["id"]}.{k} must be one of {options}')
        w = p.get('weight', 1)
        if type(w) not in (float, int) or not math.isfinite(w) or w <= 0:
            raise Invalid('Profile weights must be positive finite numbers')
    for x in c['contexts']:
        keys(x, 'id location locale budget constraints', 'context')
        keys(x.get('location'), 'label lat lon', 'location')
        string(x['location'].get('label'), 'location.label')
        string(x.get('locale'), 'context.locale')
        keys(x.get('budget'), 'amount currency basis', 'budget')
        if type(x['budget'].get('amount')) not in (float, int) or not math.isfinite(x['budget']['amount']) or x['budget']['amount'] <= 0:
            raise Invalid('budget.amount must be positive')
        for k in ('currency', 'basis'):
            string(x['budget'].get(k), 'budget.' + k)
        validate_coords(x['location'])
        if not isinstance(x.get('constraints', {}), dict):
            raise Invalid('context.constraints must be a mapping')
        for t in c['tasks']:
            for k, v in x.get('constraints', {}).items():
                if k in t.get('constraints', {}) and v != t['constraints'][k]:
                    raise Invalid(f'Conflicting constraint {k} in {t["id"]}/{x["id"]}')
    for e in entities:
        validate_coords(e.get('location', {}))
    keys(c.get('journeys'), 'mode max_turns variants', 'journeys')
    if c['journeys'].get('mode') not in ('single_turn', 'multi_turn', 'mixed'):
        raise Invalid('journeys.mode must be single_turn, multi_turn or mixed')
    positive(c['journeys'].get('max_turns'), 'max_turns', 4)
    positive(c['journeys'].get('variants'), 'variants', 4)
    if c['journeys']['mode'] == 'mixed' and c['journeys']['variants'] < 2:
        raise Invalid('Mixed mode requires at least two variants')
    if c['journeys']['mode'] != 'single_turn' and c['journeys']['max_turns'] < 2:
        raise Invalid('Multi-turn mode requires at least two turns')
    keys(c.get('sampling'), 'seed journeys repetitions', 'sampling')
    if type(c['sampling'].get('seed')) is not int:
        raise Invalid('sampling.seed must be an integer')
    positive(c['sampling'].get('journeys'), 'journeys', 10000)
    positive(c['sampling'].get('repetitions'), 'repetitions', 100)
    for e in c['engines']:
        keys(e, 'id adapter model preset model_env api_key_env search temperature max_output_tokens seed', 'engine')
        if e.get('adapter') not in ('fake', 'openai', 'perplexity'):
            raise Invalid('Supported adapters: fake, openai, perplexity')
        if e['adapter'] != 'fake' and not any(e.get(k) for k in ('model', 'model_env', 'preset')):
            raise Invalid('Live engines require model, model_env or Perplexity preset')
        if e.get('preset') and (e['adapter'] != 'perplexity' or any(e.get(k) for k in ('model', 'model_env'))):
            raise Invalid('preset is exclusive with model and only supported for Perplexity')
        if e.get('preset') and any(k in e for k in ('search', 'temperature')):
            raise Invalid('Preset controls search/settings; omit overrides to avoid hidden changes')
        if e.get('model') and e.get('model_env'):
            raise Invalid('model and model_env are exclusive')
        for field in ('model', 'model_env', 'api_key_env', 'preset'):
            if field in e:
                string(e[field], 'engine.' + field)
        if 'seed' in e and (type(e['seed']) is not int or e['adapter'] != 'fake'):
            raise Invalid('Only the fake adapter supports an integer seed')
        if 'temperature' in e and (type(e['temperature']) not in (int, float) or not math.isfinite(e['temperature']) or not 0 <= e['temperature'] <= 2):
            raise Invalid('temperature must be in 0..2; provider support still varies by model')
        if 'search' in e and type(e['search']) is not bool:
            raise Invalid('search must be boolean')
        if 'max_output_tokens' in e:
            positive(e['max_output_tokens'], 'max_output_tokens', 100000)
    keys(c.get('execution'), 'concurrency max_attempts max_requests timeout_seconds', 'execution')
    for k, upper in [('concurrency', 16), ('max_attempts', 5), ('max_requests', 100000), ('timeout_seconds', 300)]:
        positive(c['execution'].get(k), k, upper)
    keys(c.get('analysis'), 'draws seed min_journeys', 'analysis')
    positive(c['analysis'].get('draws'), 'analysis.draws', 20000)
    positive(c['analysis'].get('min_journeys'), 'analysis.min_journeys', 10000)
    if type(c['analysis'].get('seed')) is not int:
        raise Invalid('analysis.seed must be an integer')
    return c

def validate_coords(loc):
    if not isinstance(loc, dict):
        raise Invalid('location must be a mapping')
    if ('lat' in loc) != ('lon' in loc):
        raise Invalid('Latitude and longitude must appear together')
    for key, limit in [('lat', 90), ('lon', 180)]:
        if key in loc and (type(loc[key]) not in (int, float) or not math.isfinite(loc[key]) or abs(loc[key]) > limit):
            raise Invalid(f'Invalid coordinate {key}')

def load_config(path):
    try:
        return validate(yaml.safe_load(Path(path).read_text()))
    except (OSError, yaml.YAMLError, TypeError, KeyError) as exc:
        raise Invalid(f'Cannot load configuration: {exc}') from exc

def effective_engine(e):
    e = dict(e)
    if e.get('model_env'):
        value = os.environ.get(e.pop('model_env'))
        if not value:
            raise Invalid(f'Model environment variable missing for {e["id"]}')
        e['model'] = value
    return e

@dataclass
class EngineResponse:
    text: str
    raw: dict
    model: str | None
    request_id: str | None = None
    citations: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    status: str = 'completed'
    latency_ms: int = 0
    timestamp: str = field(default_factory=now)

    def data(self):
        return asdict(self)
