"""Seeded, stratified generation and a bounded deterministic conversation policy."""
from __future__ import annotations
import itertools
import random
import re
from .core import Invalid, digest

GENERATOR = 'templates-v1'
POLICY = 'refinement-v1'
OPENINGS = ('Help me {goal}.', 'I would like to {goal}. What would you suggest?',
            'What are good options to {goal}?', 'Please help me decide how to {goal}.')

def generate(config):
    rng = random.Random(config['sampling']['seed'])
    strata = {}
    total_weight = sum(p.get('weight', 1) for p in config['profiles'])
    contexts_n = len(config['contexts'])
    tasks_n = len(config['tasks'])
    for t, p, c in itertools.product(config['tasks'], config['profiles'], config['contexts']):
        sid = f'{t["id"]}/{p["id"]}/{c["id"]}'
        pool = []
        for v in range(config['journeys']['variants']):
            mode = config['journeys']['mode']
            if mode == 'mixed':
                mode = 'single_turn' if v % 2 == 0 else 'multi_turn'
            context = f"Location: {c['location']['label']}. Locale: {c['locale']}. Budget: {c['budget']['amount']} {c['budget']['currency']} {c['budget']['basis']}."
            constraints = dict(t.get('constraints', {})) | c.get('constraints', {})
            constraint_text = ' '.join(f'{k}: {v}.' for k, v in sorted(constraints.items()))
            opening = OPENINGS[v].format(goal=t['goal'])
            if p['planning_style'] == 'holistic':
                opening += ' Include this choice in a relaxed plan for the surrounding time.'
            if p['interaction_style'] == 'direct':
                opening += ' Be concise.'
            else:
                opening += ' Explain how the options would suit my situation.'
            behaviors = {
                'familiarity': {'novice': 'I am new to this area; explain the basics.', 'familiar': 'I know the area somewhat.', 'expert': 'I know the usual options; give me specific distinctions.'},
                'decision_style': {'exploratory': 'Help me explore a few possibilities.', 'comparison': 'Compare the main options and their tradeoffs.', 'decisive': 'Help me settle on one choice.'},
                'price_sensitivity': {'low': 'Quality matters more than savings within my budget.', 'medium': 'Balance quality and price.', 'high': 'Prioritize value and affordability.'},
                'specificity': {'broad': 'Start with a broad overview.', 'moderate': 'Give me a short list with useful details.', 'specific': 'Give concrete names and explain their fit.'},
                'constraint_level': {'low': 'Use my constraints as preferences.', 'medium': 'Explain any compromise on my constraints.', 'high': 'Treat my constraints as firm requirements.'},
            }
            behavior_text = ' '.join(options[p[dimension]] for dimension, options in behaviors.items())
            prompt = ' '.join([opening, behavior_text, context, constraint_text]).strip()
            if not t.get('allow_entity_names', False):
                for e in [config['entity']] + config.get('cohort', []):
                    for name in [e['name']] + e.get('aliases', []):
                        if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', prompt, re.I):
                            raise Invalid(f'Entity leakage in {sid}: {name}')
            item = {'task': t, 'profile': p, 'context': c, 'stratum': sid,
                    'weight': p.get('weight', 1) / total_weight / contexts_n / tasks_n,
                    'mode': mode, 'max_turns': 1 if mode == 'single_turn' else config['journeys']['max_turns'],
                    'prompt': prompt, 'variant': v, 'generator': GENERATOR, 'policy': POLICY}
            item['id'] = 'j_' + digest(item)[:16]
            pool.append(item)
        rng.shuffle(pool)
        strata[sid] = pool
    count = config['sampling']['journeys']
    if count < len(strata) or count > sum(map(len, strata.values())):
        raise Invalid(f'journeys must cover all {len(strata)} strata and fit the {sum(map(len, strata.values()))} distinct templates available')
    selected = []
    order = list(strata)
    rng.shuffle(order)
    while len(selected) < count:
        for sid in order:
            if strata[sid] and len(selected) < count:
                selected.append(strata[sid].pop())
    selected.sort(key=lambda j: j['id'])
    body = {'schema_version': '0.1', 'generator': GENERATOR, 'policy': POLICY,
            'seed': config['sampling']['seed'], 'journeys': selected}
    return body | {'hash': digest(body)}

def verify_bundle(bundle):
    if digest({k: v for k, v in bundle.items() if k != 'hash'}) != bundle.get('hash'):
        raise Invalid('Journey bundle hash mismatch')
    if bundle.get('generator') != GENERATOR or bundle.get('policy') != POLICY:
        raise Invalid('Unsupported generator or policy version')
    for j in bundle['journeys']:
        if j['id'] != 'j_' + digest({k: v for k, v in j.items() if k != 'id'})[:16]:
            raise Invalid('Journey content hash mismatch')

def next_message(journey, history):
    turn = sum(m['role'] == 'assistant' for m in history)
    if turn >= journey['max_turns']:
        return None
    if turn == 0:
        return journey['prompt'], 'opening'
    # Branch on an observable clarification cue, never on target success.
    if turn == 1 and re.search(r'\b(?:what is your budget|where are you staying)\b', history[-1]['content'], re.I):
        c = journey['context']
        return f"I am near {c['location']['label']}, with {c['budget']['amount']} {c['budget']['currency']} {c['budget']['basis']}. Please suggest suitable options.", 'clarification'
    followups = {
        1: ('Which options best fit the constraints I gave? Please explain the tradeoffs.', 'refinement'),
        2: ('Which of those would you personally recommend most for my situation?', 'choice'),
        3: ('Would that still be your choice if I prioritize value within my stated budget?', 'value'),
    }
    return followups[turn]
