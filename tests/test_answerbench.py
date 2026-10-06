import copy
import json
import sqlite3
import threading
import pytest
from answerbench.core import Invalid, Incompatible, EngineResponse, validate, digest, dumps, now
from answerbench.defaults import demo_config
from answerbench.journeys import generate, verify_bundle, next_message
from answerbench.engines import Adapter, FakeAdapter, EngineError, normalize, NoRedirect
from answerbench.storage import Store, exclusive_run
from answerbench.runner import create_run, execute, plan
from answerbench.extraction import extract_text, extract_run, import_review
from answerbench.metrics import estimate, report_data, compare, distance_band
from answerbench.reporting import render_report
from answerbench.cli import main

@pytest.fixture
def config():
    c = demo_config()
    c['tasks'] = c['tasks'][:1]
    c['profiles'] = c['profiles'][:1]
    c['contexts'] = c['contexts'][:1]
    c['sampling'].update(journeys=2, repetitions=2)
    c['engines'] = c['engines'][:1]
    c['analysis'].update(draws=100, min_journeys=2)
    return c

@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path/'results.sqlite')
    yield s
    s.close()


def run(store, config, factory=FakeAdapter):
    rid = create_run(store, config, generate(config))
    execute(store, rid, adapter_factory=factory, sleep=lambda _: None)
    return rid

@pytest.mark.parametrize('mutate', [
    lambda c: c.update(unknown=1),
    lambda c: c['profiles'].append(c['profiles'][0]),
    lambda c: c['contexts'][0]['location'].update(lat=100),
    lambda c: c['engines'][0].update(temperature=float('nan')),
    lambda c: c['contexts'][0].update(constraints=[]),
    lambda c: c['journeys'].update(variants=1),
    lambda c: c['engines'][0].update(model='one', model_env='TWO'),
    lambda c: c['profiles'][0].update(weight=0),
])
def test_invalid_config(config, mutate):
    mutate(config)
    with pytest.raises(Invalid): validate(config)


def test_conflicting_constraints(config):
    config['tasks'][0]['constraints'] = {'diet': 'vegan'}
    config['contexts'][0]['constraints'] = {'diet': 'seafood'}
    with pytest.raises(Invalid, match='Conflicting'): validate(config)


def test_frozen_journeys_and_leakage(config):
    a = generate(validate(config))
    assert a == generate(config)
    assert {j['mode'] for j in a['journeys']} == {'single_turn', 'multi_turn'}
    assert all(config['entity']['name'] not in j['prompt'] for j in a['journeys'])
    a['journeys'][0]['prompt'] += 'altered'
    with pytest.raises(Invalid, match='hash'): verify_bundle(a)
    config['tasks'][0]['goal'] = 'find Demo Coastal Kitchen'
    with pytest.raises(Invalid, match='leakage'): generate(config)


def test_behavior_changes_prompt(config):
    old = generate(config)
    config['profiles'][0]['price_sensitivity'] = 'high'
    new = generate(config)
    assert {j['prompt'] for j in old['journeys']} != {j['prompt'] for j in new['journeys']}


def test_policy_bound_and_clarification(config):
    j = next(j for j in generate(config)['journeys'] if j['mode']=='multi_turn')
    assert next_message(j, [{'role':'assistant','content':'What is your budget?'}])[1] == 'clarification'
    assert next_message(j, [{'role':'assistant','content':'x'}]*4) is None


def test_normalization_mixed_output():
    raw = {'status':'completed', 'id':'r', 'model':'m', 'output': [
        {'type':'reasoning','summary':[]},
        {'type':'search_results','results':[{'url':'https://example.com'}]},
        {'type':'message','content':[{'type':'output_text','text':'First','annotations':[{'type':'url_citation','url':'https://example.org'}]}]},
        {'type':'message','content':[{'type':'refusal','refusal':'Cannot answer'}]}]}
    r = normalize(raw)
    assert r.text == 'First\nCannot answer'
    assert r.citations[0]['type']=='retrieved_source'
    assert r.citations[1]['type']=='url_citation'
    assert normalize({'status':'completed','output':[]}).status=='unusable'
    assert normalize({'status':'incomplete','output':[]}).status=='incomplete'


def test_payload_and_credentials(monkeypatch):
    monkeypatch.setenv('TEST_KEY', 'secret-test-value')
    a = Adapter({'id':'o','adapter':'openai','model':'test','api_key_env':'TEST_KEY','search':True})
    a.check_credentials()
    body = a.request([{'role':'user','content':'hello'}])
    assert body['store'] is False and body['tools']==[{'type':'web_search'}]
    assert 'secret-test-value' not in dumps(body)
    assert NoRedirect().redirect_request(None,None,None,None,None,'https://other.example') is None
    p = Adapter({'id':'p','adapter':'perplexity','preset':'fast'})
    assert p.request([])['preset']=='fast'
    assert 'model' not in p.request([])


def test_resume_is_idempotent_and_sessions_isolated(store, config):
    lengths = []
    lock = threading.Lock()
    class Spy(FakeAdapter):
        def send_message(self, history, execution_key):
            with lock: lengths.append((execution_key, len(history)))
            return super().send_message(history, execution_key)
    rid = run(store, config, Spy)
    before = store.rows('SELECT * FROM turns')
    execute(store, rid, adapter_factory=Spy)
    assert store.rows('SELECT * FROM turns') == before
    groups = {}
    for k,v in lengths: groups.setdefault(k,[]).append(v)
    assert all(v==list(range(1,max(v)+1,2)) for v in groups.values())
    assert store.run(rid)['status']=='complete'
    assert len(store.rows('SELECT * FROM attempts'))==plan(config,generate(config))['response_ceiling']


def test_retry_transient_and_request_budget(store, config):
    config['execution']['max_requests']=3
    class Transient(FakeAdapter):
        def send_message(self, history, execution_key):
            if not getattr(self,'failed',False):
                self.failed=True
                raise EngineError('temporary',True)
            return super().send_message(history,execution_key)
    rid=run(store,config,Transient)
    assert len(store.rows('SELECT * FROM attempts'))==3
    assert store.run(rid)['status']=='incomplete'
    execute(store,rid,retry_failed=True,adapter_factory=Transient,sleep=lambda _:None)
    assert len(store.rows('SELECT * FROM attempts'))==3


def test_attempt_limit_survives_resume(store, config):
    class Fail(FakeAdapter):
        def send_message(self,*args): raise EngineError('temporary',True)
    rid=run(store,config,Fail)
    before=len(store.rows('SELECT * FROM attempts'))
    execute(store,rid,retry_failed=True,adapter_factory=Fail,sleep=lambda _:None)
    assert len(store.rows('SELECT * FROM attempts'))==before
    assert all(x['status']=='failed' for x in store.rows('SELECT * FROM executions'))


def test_pending_user_after_crash_is_reused(store, config):
    rid=create_run(store,config,generate(config))
    x=store.one('SELECT * FROM executions LIMIT 1')
    j=next(j for j in generate(config)['journeys'] if j['id']==x['journey_id'])
    with store.transaction() as db:
        db.execute("UPDATE executions SET status='running' WHERE id=?",(x['id'],))
        db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',(x['id'],0,'user',j['prompt'],'opening',None))
        db.execute('INSERT INTO attempts(execution_id,turn_index,attempt,started_at,status,request) VALUES(?,?,?,?,?,?)',(x['id'],1,1,now(),'started','{}'))
    execute(store,rid,adapter_factory=FakeAdapter)
    trace=store.trace(x['id'])
    assert [t['sequence'] for t in trace['turns']]==list(range(j['max_turns']*2))
    assert trace['attempts'][0]['status']=='interrupted'
    assert trace['attempts'][1]['attempt']==2


def test_extractor_abstains_and_distinguishes(config):
    entities=[config['entity'],*config['cohort']]
    entities[0]['aliases']=['Coast']
    obs,_=extract_text('I recommend Coast.',entities)
    assert obs[0]['recommended'] is None
    obs,_=extract_text('I do not recommend Demo Coastal Kitchen.',entities)
    assert obs[0]['level']=='mention' and obs[0]['recommended'] is False
    obs,candidates=extract_text('My top pick is **Demo Coastal Kitchen**. Consider **New Place**.',entities)
    assert obs[0]['strong'] is True and obs[0]['rank'] is None
    assert candidates[0]['name']=='New Place'
    obs,_=extract_text('I recommend Demo Coastal Kitchen. Avoid Demo Coastal Kitchen.',entities)
    assert obs[0]['recommended'] is None


def test_refusals_negative_failures_missing(store, config):
    class Refuse(FakeAdapter):
        def send_message(self,*args): return EngineResponse('I cannot recommend any options.',{},'test')
    rid=run(store,config,Refuse);extract_run(store,rid)
    m=report_data(store,rid)['engines'][0]['entities'][0]['recommended']
    assert m['rate']==0 and m['missing']==0
    with store.transaction() as db: db.execute("UPDATE executions SET status='failed'")
    with pytest.raises(Invalid,match='Evidence changed'): report_data(store,rid)
    extract_run(store,rid)
    m=report_data(store,rid)['engines'][0]['entities'][0]['recommended']
    assert m['rate'] is None and m['missing']==4


def test_extraction_versions_and_review(store,config):
    rid=run(store,config)
    first=extract_run(store,rid);second=extract_run(store,rid)
    assert first!=second
    r=store.one('SELECT * FROM observations WHERE batch_id=?',(second,))
    review={'execution_id':r['execution_id'],'turn_index':r['turn_index'],'entity_id':r['entity_id'],'level':'none','reviewer':'tester','evidence':[]}
    reviewed=import_review(store,rid,[review])
    assert reviewed!=second
    assert store.one('SELECT count(*) AS n FROM extraction_batches')['n']==3
    review.update(level='recommendation',evidence=[{'start':0,'end':1,'text':'WRONG'}])
    with pytest.raises(Invalid,match='span'):import_review(store,rid,[review])


def test_weighting_sparse_and_missing():
    settings={'draws':100,'seed':1,'min_journeys':2}
    expected={'a':'low','b':'low','c':'high','d':'high'}
    weights={'low':1,'high':3}
    result=estimate({'a':[0,0],'b':[1,1],'c':[1,1],'d':[1,1]},expected,weights,settings)
    assert result['rate']==.875
    assert result==estimate({'a':[0,0],'b':[1,1],'c':[1,1],'d':[1,1]},expected,weights,settings)
    assert estimate({'a':[0]},expected,weights,settings)['rate'] is None
    assert 'sparse' in estimate({'a':[0],'c':[1]},expected,weights,settings)['flags']
    assert distance_band({'lat':0,'lon':0},{'lat':0,'lon':0})=='0–1 km'


def test_paired_comparison_and_incompatibility(store, config):
    a=run(store,config);extract_run(store,a)
    b=run(store,config);extract_run(store,b)
    result=compare(store,a,b)
    assert result['matched_executions']==4
    assert result['delta_recommendation_rate']['rate']==0
    config['sampling']['seed']+=1
    c=run(store,config);extract_run(store,c)
    with pytest.raises(Incompatible,match='journey bundle'):compare(store,a,c)


def test_report_escapes_content_and_exports(store,config,tmp_path):
    class Html(FakeAdapter):
        def send_message(self,*args): return EngineResponse('<script>alert("x")</script> I recommend Demo Coastal Kitchen.',{},'test')
    rid=run(store,config,Html);extract_run(store,rid)
    path=render_report(store,rid,tmp_path/'report')
    source=open(path).read()
    assert '<script>alert(' not in source
    assert '&lt;script&gt;' in source
    assert 'Synthetic demonstration' in source
    assert json.loads((tmp_path/'report/report.json').read_text())['run_id']==rid


def test_lock_and_foreign_keys(store):
    with exclusive_run(store.path):
        with pytest.raises(Invalid,match='Another runner'):
            with exclusive_run(store.path): pass
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as db: db.execute('INSERT INTO definitions VALUES(?,?,?,?)',('missing','entity','e','{}'))


def test_cli_workflow(tmp_path,capsys):
    directory=tmp_path/'demo'
    assert main(['init',str(directory)])==0
    configpath=directory/'answerbench.yaml'
    prefix=['--config',str(configpath)]
    assert main(prefix+['validate'])==0
    assert main(prefix+['generate'])==0
    assert main(prefix+['run','--dry-run'])==0
    assert not (directory/'.answerbench/results.sqlite').exists()
    assert main(prefix+['run'])==0
    db=Store(directory/'.answerbench/results.sqlite')
    rid=db.latest();db.close()
    assert main(prefix+['extract',rid])==0
    assert main(prefix+['report',rid,'--out',str(directory/'report')])==0
    assert main(prefix+['compare',rid,rid,'--engine-a','demo_a','--engine-b','demo_b'])==0
    assert main(['init',str(directory)])==2
