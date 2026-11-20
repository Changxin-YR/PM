import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from secrets import token_urlsafe
import sys
from uuid import uuid4

from argon2 import PasswordHasher
import httpx
import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from pmo import schema_v1 as db
from pmo.projects import fingerprint

OPS = ('pmo_gate_check', 'pmo_review_prepare', 'pmo_review_list', 'pmo_review_submit')
CHARTER = {'valueProp': 'Review the demo', 'targetClient': 'Synthetic client', 'attractiveness': 'Measured trial',
           'expectedBenefit': 'Reliable exports', 'successCriteria': 'Trial completed', 'roughCost': '100.00',
           'roughEffort': '10.00', 'roughDuration': 12, 'topRisks': [{'statement': 'Late access', 'response': 'Confirm dates'}]}


def headers(rev=1):
    return {'If-Match': str(rev), 'Idempotency-Key': uuid4().hex}


def data(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def member(c, pid, p, assigned_by, role):
    return c.scalar(db.project_members.insert().values(project_id=pid, person_id=p, assigned_by=assigned_by,
        role=role, assignment_status='accepted', accepted_at=sa.func.now()).returning(db.project_members.c.id))


@pytest.fixture
def team(live, actor):
    engine, api, port, _ = live
    token, credential = actor(extra_actions=OPS)
    bearer = {'Authorization': 'Bearer ' + token}
    project = data(api.post('/api/v1/projects', headers={**bearer, **headers()}, json={'code': 'REVIEWS', 'name': 'Synthetic review'}))
    pid, org = project['projectId'], project['orgId']
    password = token_urlsafe(28)
    digest = PasswordHasher().hash(password)
    people, names = {}, {}
    with engine.begin() as c:
        uid = c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id == credential))
        people['initiator'] = p = c.scalar(sa.select(db.persons.c.id).where(db.persons.c.user_id == uid))
        c.execute(db.user_roles.insert().values(user_id=uid, role='delegator'))
        names['initiator'] = c.scalar(db.user_accounts.update().where(db.user_accounts.c.id == uid)
                                     .values(password_hash=digest).returning(db.user_accounts.c.username))
        for role in ('pm', 'sponsor', 'reviewer', 'client_representative', 'outsider'):
            names[role] = name = uuid4().hex
            users = db.user_accounts
            uid = c.scalar(users.insert().values(username=name, password_hash=digest, is_active=True).returning(users.c.id))
            people[role] = person = uuid4()
            c.execute(db.persons.insert().values(id=person, org_id=org, user_id=uid, created_by=person, name=role, is_active=True))
            if role != 'outsider': member(c, pid, person, p, role)
            if role == 'pm': c.execute(db.user_roles.insert().values(user_id=uid, role='delegator'))
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(pm_id=people['pm'], sponsor_id=people['sponsor']))
        c.execute(db.stakeholders.insert().values(project_id=pid, created_by=p, name='Synthetic client', type='customer',
                  linked_person_id=people['client_representative']))
    sessions = {}
    try:
        for role, name in names.items():
            sessions[role] = client = httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/', trust_env=False)
            client.headers['X-CSRF-Token'] = data_login(client, name, password)
        yield pid, people, sessions, bearer
    finally:
        for session in sessions.values(): session.close()


def data_login(client, username, password):
    response = client.post('auth/login', json={'username': username, 'password': password})
    response.raise_for_status()
    return response.json()['csrfToken']


def issue(client, pid, actions=OPS):
    revision = data(client.get('me/agent-tokens'))['collectionRevision']
    result = client.post('me/agent-tokens', headers=headers(revision), json={'allowedActions': list(actions), 'allowedProjectIds': [pid],
                         'expiresAt': (datetime.now(UTC) + timedelta(minutes=20)).isoformat()})
    data(result)
    return result.headers['X-PMO-New-Token']


def read_gaps(client, path, gate):
    return {gap['code'] for gap in data(client.get(path + '/gates/check', params={'gate': gate}))['missing']}


def count_state(c, pid):
    return c.execute(sa.text('''SELECT
        (SELECT count(*) FROM reviews WHERE project_id=:p),
        (SELECT count(*) FROM review_submission_snapshots s JOIN reviews r ON r.id=s.review_id WHERE r.project_id=:p),
        (SELECT count(*) FROM project_stage_transitions WHERE project_id=:p),
        (SELECT count(*) FROM audit_logs WHERE project_id=:p AND action='pmo_review_submit' AND result='success'),
        (SELECT count(*) FROM idempotency_keys i JOIN reviews r ON i.resource_id=r.id WHERE r.project_id=:p AND operation='pmo_review_submit'),
        (SELECT count(*) FROM outbox_events WHERE payload->>'projectId'=CAST(:p AS text) AND event_type='review.submitted')'''), {'p': pid}).one()


def test_charter_read_only_submit_replay_and_rollback(live, team, actor):
    engine, api, _, _ = live
    pid, people, users, auto = team
    path = f'projects/{pid}'
    owner, pm, sponsor = (users[r] for r in ('initiator', 'pm', 'sponsor'))
    key = headers()
    body = {'reviewType': 'charter'}
    assert api.get('/api/v1/' + path + '/reviews', headers=auto).json()['code'] == 'EXPLICIT_PROJECT_GRANT_REQUIRED'
    assert api.post('/api/v1/' + path + '/reviews', headers={**key, **auto}, json=body).json()['code'] == 'EXPLICIT_PROJECT_GRANT_REQUIRED'
    assert users['outsider'].get(path + '/reviews').status_code == 404
    foreign, _ = actor(extra_actions=OPS)
    assert api.get('/api/v1/' + path + '/reviews', headers={'Authorization': 'Bearer ' + foreign}).status_code == 404
    assert read_gaps(owner, path, 'charter') == {'CHARTER_MISSING'}
    denied = owner.post(path + '/reviews', headers=key, json=body)
    assert denied.json()['code'] == 'GATE_REQUIREMENTS_NOT_MET'
    with engine.connect() as c:
        row = c.execute(sa.select(db.audit_logs).where(db.audit_logs.c.request_id==denied.headers['X-Request-ID'])).mappings().one()
        assert (row.result, str(row.project_id), row.error_code) == ('denied', pid, 'GATE_REQUIREMENTS_NOT_MET')
    data(owner.put(path + '/charter', headers=headers(), json={'fieldsCharter': CHARTER}))
    with engine.begin() as c:
        member(c, pid, people['pm'], people['initiator'], 'reviewer')
        member(c, pid, people['initiator'], people['initiator'], 'reviewer')
        c.execute(db.project_members.update().where(db.project_members.c.project_id == pid,
                  db.project_members.c.person_id == people['reviewer']).values(revoked_at=sa.func.now()))
    assert read_gaps(owner, path, 'charter') == {'INDEPENDENT_REVIEWER_REQUIRED'}
    with engine.begin() as c:
        c.execute(db.project_members.update().where(db.project_members.c.project_id == pid,
                  db.project_members.c.person_id.in_([people['initiator'], people['reviewer']]),
                  db.project_members.c.role == 'reviewer').values(revoked_at=sa.case(
                      (db.project_members.c.person_id == people['initiator'], sa.func.now()), else_=None)))
    for name in ('prepare', 'check'):
        response = owner.get(path + ('/reviews/prepare' if name == 'prepare' else '/gates/check'), params={'gate': 'charter'})
        checked = data(response)
        if name == 'prepare':
            assert response.headers['X-PMO-Can-Submit'] == 'true' and response.headers['X-PMO-Project-Revision'] == '1'
            prepared = checked['gateCheck']
        else: assert checked == prepared
    assert prepared['eligible'] and data(owner.get(path + '/reviews'))['total'] == 0
    assert sponsor.get(path + '/reviews/prepare?gate=charter').headers['X-PMO-Can-Submit'] == 'false'
    with engine.connect() as c: assert count_state(c, pid) == (0, 0, 0, 0, 0, 0)
    for bad, h in [({**body, 'stage': 'closed'}, key), (body, {'If-Match': '1'}), (body, {'Idempotency-Key': key['Idempotency-Key']})]:
        assert owner.post(path + '/reviews', headers=h, json=bad).json()['code'] == 'VALIDATION_FAILED'
    assert sponsor.post(path + '/reviews', headers=key, json=body).status_code == 403
    assert pm.post(path + '/reviews', headers=key, json={'reviewType': 'feasibility'}).json()['code'] == 'GATE_STAGE_MISMATCH'
    assert owner.post(path + '/reviews', headers=headers(3), json=body).json()['code'] == 'VERSION_CONFLICT'
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER review_outbox_failure BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        assert owner.post(path + '/reviews', headers=key, json=body).json()['code'] == 'DATABASE_FAILURE'
        assert data(owner.get(path))['project']['stage'] == 'drafting'
        assert data(owner.get(path))['project']['revision'] == 1
        with engine.connect() as c: assert count_state(c, pid) == (0, 0, 0, 0, 0, 0)
    finally:
        with engine.begin() as c: c.exec_driver_sql('DROP TRIGGER review_outbox_failure ON outbox_events')
    sent = owner.post(path + '/reviews', headers=key, json=body)
    row = data(sent)
    assert row['status'] == 'pending' and 'verdict' not in row
    assert owner.post(path + '/reviews', headers=key, json=body).json() == sent.json()
    assert owner.post(path + '/reviews', headers={**key, 'If-Match': '2'}, json=body).json()['code'] == 'IDEMPOTENCY_CONFLICT'
    assert pm.post(path + '/reviews', headers=headers(2), json=body).json()['code'] == 'GATE_STAGE_MISMATCH'
    assert data(owner.get(path))['project']['stage'] == 'charter_review'
    assert data(owner.get('projects'))['items'][0]['revision'] == 2
    assert data(owner.get(path + '/reviews'))['items'] == [row]
    assert owner.get(path + '/requirements').status_code == 404
    assert api.get('/api/v1/' + path, headers=auto).status_code == 404
    assert read_gaps(owner, path, 'charter') == {'GATE_STAGE_MISMATCH'}
    with engine.connect() as c:
        assert count_state(c, pid) == (1, 1, 1, 1, 1, 1)
        stored = c.execute(sa.text('''SELECT s.materials,s.snapshot_hash,r.snapshot_hash AS review_hash,t.from_stage,t.to_stage,t.to_revision,t.request_id
            FROM review_submission_snapshots s JOIN reviews r ON r.id=s.review_id JOIN project_stage_transitions t ON t.review_id=r.id
            WHERE r.project_id=:p'''), {'p': pid}).mappings().one()
        assert stored.snapshot_hash == stored.review_hash == prepared['snapshotHash'] == fingerprint(stored.materials)
        assert [r['person_id'] for r in stored.materials['reviewers']] == [str(people['reviewer'])]
        assert (stored.from_stage, stored.to_stage, stored.to_revision, str(stored.request_id)) == ('drafting', 'charter_review', 2, sent.headers['X-Request-ID'])
    for table in ('review_submission_snapshots', 'project_stage_transitions'):
        with pytest.raises(DBAPIError), engine.begin() as c: c.exec_driver_sql(f'DELETE FROM {table} WHERE false')
    with engine.begin() as c:
        c.execute(db.project_members.update().where(db.project_members.c.project_id == pid,
                  db.project_members.c.role == 'initiator').values(revoked_at=sa.func.now()))
    assert owner.post(path + '/reviews', headers=key, json=body).status_code == 404


def planning_setup(engine, pid, people, users):
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='designing'))
    path = f'projects/{pid}'
    pm = users['pm']
    req = data(pm.post(path + '/requirements', headers=headers(), json={'desc': 'Export results', 'acceptanceCriteria': 'Contains IDs',
               'priority': 'P0', 'plannedConfirmAt': '2026-12-01'}))
    for i in range(1, 4):
        data(pm.post(path + '/milestones', headers=headers(), json={'name': f'Step {i}', 'ordinal': i, 'plannedDate': f'2026-12-0{i}', 'isKey': True}))
    for rev, kind, budget in [(1, 'money', '100'), (2, 'effort', '10')]:
        data(pm.put(path + '/cost-budgets', headers=headers(rev), json={'type': kind, 'budget': budget}))
    risk = data(pm.post(path + '/risks', headers=headers(), json={'statement': 'Unresolved delivery risk', 'response': 'Documented response',
                'prob': 2, 'impact': 3, 'isBlocking': True}))
    assert 'SIGNED_REQUIREMENTS_REQUIRED' in read_gaps(pm, path, 'feasibility')
    url = path + '/requirement-baseline'
    for role in ('pm', 'client_representative'):
        who = users[role]
        baseline = data(who.get(url))
        data(who.post(url + '/sign', headers=headers(baseline['revision']), json={'contentHash': baseline['contentHash'],
                      'role': role, 'reason': 'Reviewed isolated test baseline'}))
    return path, req['requirementId'], risk['riskId']


def test_feasibility_signed_planning_and_pm_race(live, team):
    engine, api, _, _ = live
    pid, people, users, _ = team
    path, _, _ = planning_setup(engine, pid, people, users)
    pm, sponsor, owner = (users[r] for r in ('pm', 'sponsor', 'initiator'))
    assert read_gaps(pm, path, 'feasibility') == set()
    assert owner.get(path + '/reviews/prepare?gate=feasibility').status_code == 404
    command = {'reviewType': 'feasibility'}
    assert sponsor.post(path + '/reviews', headers=headers(), json=command).status_code == 403
    token = issue(pm, pid)
    agent = {'Authorization': 'Bearer ' + token}
    def submit_as(index):
        return (pm if index == 0 else api).post(path + '/reviews' if index == 0 else '/api/v1/' + path + '/reviews',
                                                headers={**headers(), **(agent if index else {})}, json=command)
    with ThreadPoolExecutor(2) as pool: responses = list(pool.map(submit_as, (0, 1)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert next(r for r in responses if not r.is_success).json()['code'] == 'VERSION_CONFLICT'
    assert data(pm.get(path))['project']['stage'] == 'feasibility_review'
    with engine.connect() as c: assert count_state(c, pid) == (1, 1, 1, 1, 1, 1)


def test_closing_verified_postings_and_free_text_do_not_clear_gates(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    path, _, risk = planning_setup(engine, pid, people, users)
    pm, reviewer = people['pm'], people['reviewer']
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='delivering'))
        c.execute(db.risks.update().where(db.risks.c.id == risk).values(handoff_to_id=reviewer, response='Receiver accepted (unverified note)'))
        for kind, amount in [('money', 80), ('effort', 12)]:
            entry = c.scalar(db.cost_entries.insert().values(project_id=pid, created_by=pm, type=kind, category='Synthetic',
                period_start='2024-01-01', period_end='2024-01-31', baseline_version=1, budget=100 if kind == 'money' else 10,
                actual=999, forecast_remaining=0).returning(db.cost_entries.c.id))
            c.execute(db.cost_postings.insert().values(project_id=pid, created_by=pm, cost_entry_id=entry, type=kind, source_type=kind,
                period='2024-01', amount_signed=amount, source_ref='Synthetic verified posting', occurred_at='2024-01-02T00:00:00Z',
                status='verified', verified_at=sa.func.now(), verified_by=reviewer, source_fingerprint=fingerprint([kind, risk]),
                verification_note='Reconciled (unverified note)'))
    manager = users['pm']
    assert read_gaps(manager, path, 'closing') == {'CUSTOMER_ACCEPTANCE_REQUIRED', 'MILESTONE_NOT_COMPLETED',
        'BLOCKING_RISK_UNRESOLVED', 'COST_RECONCILIATION_REQUIRED'}
    with engine.begin() as c:
        c.execute(db.cost_entries.update().where(db.cost_entries.c.project_id==pid,db.cost_entries.c.type=='effort')
                  .values(budget=0,forecast_remaining=1,source_ref='Zero budget declared in text'))
    assert {'EFFORT_CLOSING_BUDGET_UNVERIFIED', 'EFFORT_FORECAST_NOT_ZERO'} <= read_gaps(manager, path, 'closing')
    assert manager.post(path + '/reviews', headers=headers(), json={'reviewType': 'closing', 'signedException': True}).status_code == 422
    assert manager.post(path + '/reviews', headers=headers(), json={'reviewType': 'closing'}).json()['code'] == 'GATE_REQUIREMENTS_NOT_MET'
    assert users['sponsor'].post(path + '/reviews', headers=headers(), json={'reviewType': 'closing'}).status_code == 403
    assert data(manager.get(path))['project']['stage'] == 'delivering'
    with engine.connect() as c: assert count_state(c, pid) == (0, 0, 0, 0, 0, 0)


def test_reviews_stdio_closed_dtos_and_explicit_initiator_replay(live, team):
    _, _, port, _ = live
    pid, _, users, _ = team
    owner = users['initiator']
    path = f'projects/{pid}'
    data(owner.put(path + '/charter', headers=headers(), json={'fieldsCharter': CHARTER}))
    token = issue(owner, pid)
    env = {**{k: os.environ[k] for k in ('SYSTEMROOT', 'PATH', 'TEMP', 'TMP') if k in os.environ}, 'PMO_API_PORT': str(port),
           'PMO_AGENT_TOKEN': token, 'PYTHONUTF8': '1'}

    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'pmo.mcp_server'], env=env)
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(OPS) <= tools.keys()
            async def call(op, args, error=None):
                response = await session.call_tool(op, {'projectId': pid, **args})
                payload = response.structuredContent
                cls = jsonschema.Draft202012Validator
                cls(tools[op].outputSchema, format_checker=cls.FORMAT_CHECKER).validate(payload)
                assert bool(response.isError) == bool(error)
                if error: assert payload['error']['code'] == error
                return payload
            check = (await call(OPS[0], {'gate': 'charter'}))['data']
            assert check['eligible']
            assert (await call(OPS[1], {'gate': 'charter'}))['data']['gateCheck'] == check
            assert (await call(OPS[2], {}))['data']['total'] == 0
            args = {'reviewType': 'charter', 'expectedRevision': 1, 'idempotencyKey': uuid4().hex}
            await call(OPS[3], {**args, 'verdict': 'pass'}, 'VALIDATION_FAILED')
            submitted = await call(OPS[3], args)
            assert submitted == await call(OPS[3], args)
            assert (await call(OPS[2], {}))['data']['items'] == [submitted['data']]
            assert (await call(OPS[0], {'gate': 'charter'}))['data']['missing'][0]['code'] == 'GATE_STAGE_MISMATCH'

    asyncio.run(scenario())
