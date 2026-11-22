import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import sys
from uuid import uuid4

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sqlalchemy as sa
from pmo import schema_v1 as db
from test_lifecycle import execution_fixture
from test_reviews import data, headers, issue, team as team


def test_cost_snapshot_scope_revision_and_authority(live,actor):
    engine,api,_,_=live
    ops=('pmo_cost_summary','pmo_cost_budget_draft_save','pmo_cost_forecast_save')
    token,_=actor(extra_actions=ops); outside,_=actor(extra_actions=ops)
    def h(rev=1,secret=token):
        return {'Authorization':'Bearer '+secret,'Idempotency-Key':uuid4().hex,'If-Match':str(rev)}
    pid=api.post('/api/v1/projects',headers=h(),json={'code':'COST','name':'Cost versions'}).json()['data']['projectId']
    path=f'/api/v1/projects/{pid}/cost'
    def summary(**params):
        r=api.get(path+'s',headers=h(),params=params); r.raise_for_status(); return r.json()
    assert api.get(path+'s',headers=h(secret=outside)).status_code==404
    body={'type':'money','budget':'125.04'}; key=h()
    first=api.put(path+'-budgets',headers=key,json=body)
    assert first.json()['data']['revision']==2 and api.put(path+'-budgets',headers=key,json=body).json()==first.json()
    assert api.put(path+'-budgets',headers=h(),json=body).status_code==409
    for val in ('0.001','-1','1e5',5):
        assert api.put(path+'-budgets',headers=h(2),json={**body,'budget':val}).status_code==422
    assert summary()['data']['money']['color']=='untrusted'
    body={'type':'money','forecastRemaining':'50','reason':'Estimate updated'}
    assert api.put(path+'-forecasts',headers=h(2),json=body).json()['data']['revision']==3
    with engine.begin() as c:
        person=c.scalar(sa.select(db.projects.c.created_by).where(db.projects.c.id==pid))
        verifier=uuid4()
        c.execute(db.persons.insert().values(id=verifier,created_by=person,org_id=c.scalar(sa.select(db.projects.c.org_id).where(db.projects.c.id==pid)),name='Verifier',is_active=True))
        args=dict(project_id=pid,created_by=person,type='money',category='fixture',period_start=date(2020,11,1),
                  period_end=date(2020,11,30),budget=100,actual=999,forecast_remaining=0,baseline_version=1)
        original=c.scalar(db.cost_entries.insert().values(**args).returning(db.cost_entries.c.id))
        post=dict(project_id=pid,created_by=person,cost_entry_id=original,occurred_at=datetime(2020,11,5,tzinfo=UTC),type='money',
                  period='2020-11',source_type='money',source_ref='synthetic-proof')
        checked=dict(status='verified',verified_at=sa.func.now(),verified_by=verifier,verification_note='Checked fixture')
        positive=c.scalar(db.cost_postings.insert().values(**post,**checked,amount_signed=100,source_fingerprint='1'*64).returning(db.cost_postings.c.id))
        c.execute(db.cost_postings.insert().values(**post,**checked,amount_signed=-100,source_fingerprint='2'*64,posting_of_id=positive,reversal_reason='Correction'))
        c.execute(db.cost_postings.insert().values(**post,**checked,amount_signed=70,source_fingerprint='3'*64))
        c.execute(db.cost_postings.insert().values(**post,amount_signed=555,status='pending'))
    old=summary()['data']; m=old['money']
    assert m=={'budget':'100.00','actual':'70.00','forecastRemaining':'50.00','projectedTotal':'120.00','varianceRate':'0.2000','color':'red'}
    with engine.begin() as c:
        c.execute(db.cost_entries.update().where(db.cost_entries.c.id==original).values(is_current_baseline=False))
        c.execute(db.cost_entries.insert().values(**{**args,'budget':150,'baseline_version':2}))
        c.execute(db.cost_postings.insert().values(**post,**checked,amount_signed=30,source_fingerprint='4'*64))
    current=summary()['data']['money']
    assert current['budget']=='150.00' and current['actual']=='100.00' and current['color']=='green'
    assert summary(asOf=old['asOf'])['data']==old
    assert summary()['data']['effort']['color']=='untrusted'
    assert api.get(path+'s',headers=h(),params={'asOf':'2099-01-01T00:00:00Z'}).status_code==422
    with engine.connect() as c:
        assert c.execute(sa.text('SELECT revision,money_budget,reason FROM cost_plan_versions WHERE project_id=:p ORDER BY revision'),{'p':pid}).all()==[(2,Decimal('125.04'),None),(3,Decimal('125.04'),'Estimate updated')]


def test_approved_cost_bases_switch_at_one_effective_instant(live, team):
    engine, api, port, _ = live
    pid, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    project, _, _ = execution_fixture(live, team)
    uri = project + '/costs'
    with engine.begin() as c:
        initial = dict(c.execute(sa.text('''SELECT e.type, h.effective_at FROM cost_entries e JOIN cost_baseline_history h
            ON h.entry_id=e.id WHERE e.project_id=:p AND e.baseline_version=1 AND h.is_current'''), {'p': pid}).all())
        old_id = c.scalar(sa.select(db.cost_entries.c.id).where(db.cost_entries.c.project_id == pid,
                                                               db.cost_entries.c.type == 'money'))
        c.execute(sa.text('''INSERT INTO change_approval_grants(project_id,person_id,target_type,authority_ref,expires_at)
            VALUES(:p,:u,'budget','Synthetic independent baseline authority',clock_timestamp()+interval '1 hour')'''),
            {'p': pid, 'u': people['reviewer']})
    now = datetime.now(UTC)
    posting = data(pm.post(project + '/cost-postings', headers=headers(), json={
        'type': 'money', 'amount': '30.25', 'period': now.strftime('%Y-%m'), 'sourceRef': 'Synthetic ledger item 301'}))
    data(reviewer.post(f"cost-postings/{posting['postingId']}/verify", headers=headers(),
                       json={'reason': 'Compared with the synthetic source', 'occurredAt': now.isoformat()}))
    change = data(pm.post(project + '/changes', headers=headers(), json={
        'targetType': 'budget', 'targetId': str(old_id), 'before': {'budget': '100.00'}, 'after': {'budget': '140.25'},
        'reason': 'Documented increase maintaining historic actuals.'}))
    pending = data(pm.get(uri))
    assert (pending['money']['budget'], pending['money']['actual']) == ('100.00', '30.25')
    path = f"changes/{change['changeId']}/approve"
    rationale = {'rationale': 'Verified the independent approval and the replacement budget.'}
    assert users['sponsor'].post(path, headers=headers(change['revision']), json=rationale).status_code == 403
    key = headers(change['revision'])
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER test_budget_outbox_fault BEFORE INSERT ON outbox_events '
                          'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        failed = reviewer.post(path, headers=key, json=rationale)
        assert (failed.status_code, failed.json()['code']) == (500, 'DATABASE_FAILURE')
        rid = failed.headers['X-Request-ID']
        assert data(pm.get(uri))['money'] == pending['money']
        with engine.connect() as c:
            assert c.scalar(sa.text('SELECT count(*) FROM change_decision_records WHERE change_id=:id'),
                            {'id': change['changeId']}) == 0
            assert c.execute(sa.text('SELECT status,effective_at FROM change_entries WHERE id=:id'),
                             {'id': change['changeId']}).one() == ('pending', None)
            assert c.scalar(sa.text('SELECT count(*) FROM cost_baseline_history WHERE entry_id=:id'), {'id': old_id}) == 1
            assert c.scalar(sa.select(db.audit_logs.c.id).where(db.audit_logs.c.request_id == rid,
                                                               db.audit_logs.c.result == 'success')) is None
            assert c.scalar(sa.select(db.outbox_events.c.id).where(db.outbox_events.c.dedupe_key == rid)) is None
            assert c.scalar(sa.select(db.idempotency_keys.c.id).where(
                db.idempotency_keys.c.idempotency_key == key['Idempotency-Key'])) is None
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER test_budget_outbox_fault ON outbox_events')
    approved = reviewer.post(path, headers=key, json=rationale)
    at = datetime.fromisoformat(data(approved)['effectiveAt'])
    assert reviewer.post(path, headers=key, json=rationale).json() == approved.json()
    with engine.connect() as c:
        retired = c.scalar(sa.text('SELECT effective_at FROM cost_baseline_history WHERE entry_id=:e AND NOT is_current'), {'e': old_id})
        entries = c.execute(sa.text('''SELECT e.id,e.baseline_version,e.is_current_baseline,h.effective_at
            FROM cost_entries e JOIN cost_baseline_history h ON h.entry_id=e.id AND h.is_current
            WHERE e.project_id=:p AND e.category='project_baseline' AND e.type='money' ORDER BY e.baseline_version'''), {'p': pid}).all()
        assert [(r.baseline_version, r.is_current_baseline) for r in entries] == [(1, False), (2, True)]

    def history(moment):
        response = pm.get(uri, params={'asOf': moment.isoformat()})
        data(response)
        return response.json()

    at_retirement = history(retired)['data']['money']
    assert at_retirement['budget'] == ('140.25' if retired >= at else '100.00'), at_retirement
    assert retired == entries[1].effective_at == at
    assert initial['money'] == initial['effort']
    floor = history(initial['money'])['data']
    assert (floor['money']['budget'], floor['effort']['budget']) == ('100.00', '10.00')
    old = history(at - timedelta(microseconds=1))
    new = history(at)
    assert (old['data']['money']['budget'], new['data']['money']['budget']) == ('100.00', '140.25')
    assert old['data']['effort'] == new['data']['effort']
    assert old['data']['money']['actual'] == new['data']['money']['actual'] == '30.25'
    assert any(ref.startswith(f'cost-entry:{old_id}@') for ref in old['meta']['sourceRefs'])
    assert not any(ref.startswith(f'cost-entry:{old_id}@') for ref in new['meta']['sourceRefs'])
    assert any(ref.startswith(f'cost-entry:{entries[1].id}@') for ref in new['meta']['sourceRefs'])

    credential = issue(pm, pid, ('pmo_cost_summary',))
    params = StdioServerParameters(command=sys.executable, args=['-m', 'pmo.mcp_server'],
                                   env={'PMO_AGENT_TOKEN': credential, 'PMO_API_PORT': str(port), 'PYTHONUTF8': '1'})

    async def check_stdio():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as cli:
                await cli.initialize()
                for expected in (old, new):
                    cutoff = expected['data']['asOf']
                    agent = api.get('/api/v1/' + uri, params={'asOf': cutoff}, headers={'Authorization': f'Bearer {credential}'})
                    data(agent)
                    result = await cli.call_tool('pmo_cost_summary', {'projectId': pid, 'asOf': cutoff})
                    assert not result.isError and result.structuredContent == {'ok': True, **agent.json()}
                    assert result.structuredContent['meta'] == expected['meta']
                    assert result.structuredContent['data'] == {key: expected['data'][key]
                        for key in ('projectId', 'money', 'effort', 'asOf', 'ruleVersion')}

    asyncio.run(check_stdio())


def test_cost_summary_contract_handles_unavailable_rate(live, actor):
    _, api, _, _ = live
    token, _ = actor(extra_actions=('pmo_cost_summary',))
    auth = {'Authorization': f'Bearer {token}'}
    pid = data(api.post('/api/v1/projects', headers={**auth, **headers()},
                        json={'code': 'COST-DTO', 'name': 'Synthetic missing cost baseline'}))['projectId']
    response = api.get(f'/api/v1/projects/{pid}/costs', headers=auth)
    snapshot = data(response)
    spec = api.get('/openapi.json').raise_for_status().json()
    schema = spec['paths']['/api/v1/projects/{projectId}/costs']['get']['responses']['200']['content']['application/json']['schema']
    validator = Draft202012Validator({**schema, 'components': spec['components']})
    assert all('varianceRate' not in snapshot[k] and snapshot[k]['color'] == 'untrusted' for k in ('money', 'effort'))
    validator.validate(response.json())
    for fields in ({'actual': '1e2'}, {'budget': 0}, {'projectedTotal': '0.001'}, {'currency': 'USD'},
                   {'varianceRate': 'NaN'}, {'color': 'unknown'}):
        broken = {**snapshot, 'money': {**snapshot['money'], **fields}}
        assert not validator.is_valid({**response.json(), 'data': broken}), fields
