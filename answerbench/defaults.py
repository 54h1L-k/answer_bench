"""Fictional, offline starter benchmark. Never represents live visibility."""
from .core import DIMS

def demo_config():
    profiles = []
    for ident, holistic in [('planner', True), ('searcher', False)]:
        p = {'id': ident, 'weight': 1, **{k: v[0] for k, v in DIMS.items()}}
        p.update(planning_style='holistic' if holistic else 'atomic', interaction_style='conversational' if holistic else 'direct',
                 decision_style='exploratory' if holistic else 'decisive', specificity='broad' if holistic else 'specific')
        profiles.append(p)
    return {
        'schema_version': '0.1', 'project': {'name': 'AnswerBench fictional coastal demo', 'database': '.answerbench/results.sqlite'},
        'entity': {'id': 'coastal', 'name': 'Demo Coastal Kitchen', 'type': 'restaurant', 'aliases': [], 'location': {'lat': 15.0, 'lon': 74.0}},
        'cohort': [{'id': 'harbor', 'name': 'Harbor Table', 'type': 'restaurant'}, {'id': 'palm', 'name': 'Palm Courtyard', 'type': 'restaurant'}],
        'tasks': [{'id': 'dinner', 'goal': 'Find a seafood dinner', 'intent': 'discovery', 'category': 'restaurant'},
                  {'id': 'evening', 'goal': 'Plan an evening with dinner and a walk', 'intent': 'planning', 'category': 'restaurant'}],
        'profiles': profiles,
        'contexts': [{'id': ident, 'location': {'label': label, 'lat': lat, 'lon': 74.0}, 'locale': 'en-IN',
                      'budget': {'amount': 1500, 'currency': 'INR', 'basis': 'per person'}}
                     for ident, label, lat in [('near', 'Fictional coast, near the harbor', 15.004), ('mid', 'Fictional coast, inland village', 15.03), ('far', 'Fictional coast, northern bay', 15.12)]],
        'journeys': {'mode': 'mixed', 'max_turns': 4, 'variants': 2},
        'sampling': {'seed': 42, 'journeys': 24, 'repetitions': 3},
        'engines': [{'id': 'demo_a', 'adapter': 'fake', 'seed': 1}, {'id': 'demo_b', 'adapter': 'fake', 'seed': 2}],
        'execution': {'concurrency': 4, 'max_attempts': 2, 'max_requests': 1000, 'timeout_seconds': 60},
        'analysis': {'seed': 19, 'draws': 1000, 'min_journeys': 10},
    }
