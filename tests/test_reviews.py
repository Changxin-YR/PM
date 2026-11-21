import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from secrets import token_urlsafe
import sys
from types import SimpleNamespace
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
from pmo.review_scores import ANCHORS, WEIGHTS, FeasibilityDimensions, assess

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


def planning_setup(engine, pid, people, users, *, seed_stage=True, priority='P0', first_day=None, effort_budget='10'):
    if seed_stage:
        with engine.begin() as c:
            c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='designing'))
    path = f'projects/{pid}'
    pm = users['pm']
    req = data(pm.post(path + '/requirements', headers=headers(), json={'desc': 'Export results', 'acceptanceCriteria': 'Contains IDs',
               'priority': priority, 'plannedConfirmAt': first_day.isoformat() if first_day else '2026-12-01'}))
    for i in range(1, 4):
        day = (first_day + timedelta(days=i-1)).isoformat() if first_day else f'2026-12-0{i}'
        data(pm.post(path + '/milestones', headers=headers(), json={'name': f'Step {i}', 'ordinal': i, 'plannedDate': day, 'isKey': True}))
    for rev, kind, budget in [(1, 'money', '100'), (2, 'effort', effort_budget)]:
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
    token = issue(owner, pid, actions=(*OPS, 'pmo_decision_get'))
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
            review_id = submitted['data']['reviewId']
            scored = users['reviewer']
            score(scored, f'reviews/{review_id}', [4, 4, 4, 4])
            final = data(decide(scored, f'reviews/{review_id}'))
            response = await session.call_tool('pmo_decision_get', {'reviewId': review_id})
            assert not response.isError
            payload = response.structuredContent
            jsonschema.Draft202012Validator(tools['pmo_decision_get'].outputSchema).validate(payload)
            api_result = data(owner.get(f'reviews/{review_id}/decisions'))
            assert payload['data']['items'] == api_result['items']
            assert payload['data']['items'][0]['decisionId'] == final['decision']['decisionId']
            assert 'rationale' not in payload['data']['items'][0]

    asyncio.run(scenario())


def start_charter(pid, users):
    owner = users['initiator']
    path = f'projects/{pid}'
    old = data(owner.get(path))['project']
    charter = data(owner.get(path + '/charter'))
    data(owner.put(path + '/charter', headers=headers(charter['expectedRevision']), json={'fieldsCharter': CHARTER}))
    review = data(owner.post(path + '/reviews', headers=headers(old['revision']), json={'reviewType': 'charter'}))
    return f"reviews/{review['reviewId']}"


def score_command(state, values, *, source=None):
    return {'reviewType': state['review']['type'], 'round': state['round'], 'dimensions': {
        dimension['key']: {'score': value, 'selectedAnchorCode': ANCHORS[value-1], 'rationale': 'Compared with the original anchors.',
                           'evidenceRefs': [source or next(ref for ref in state['materialRefs'] if ref.startswith(
                               'charter:' if state['review']['type'] == 'charter' else 'milestone:'))]}
        for dimension, value in zip(state['dimensions'], values, strict=True)}}


def bench(client, path):
    return data(client.get(path + '/workbench'))


def score(client, path, values):
    state = bench(client, path)
    return data(client.post(path + '/scores', headers=headers(state['review']['revision']), json=score_command(state, values)))


def outcome(state, verdict='pass', **kwargs):
    return {'verdict': verdict, 'rationale': 'Independent decision: reviewed source materials.',
            'approvalRequestId': state['approvalRequestId'], **kwargs}


def decide(client, path, **kwargs):
    state = bench(client, path)
    return client.post(path + '/decisions', headers=headers(state['review']['revision']), json=outcome(state, **kwargs))


def result_counts(c, pid):
    return c.execute(sa.text('''SELECT
        (SELECT stage FROM projects WHERE id=:p),
        (SELECT count(*) FROM decisions WHERE project_id=:p),
        (SELECT count(*) FROM review_decision_snapshots s JOIN reviews r ON s.review_id=r.id WHERE r.project_id=:p),
        (SELECT count(*) FROM project_stage_transitions WHERE project_id=:p),
        (SELECT count(*) FROM audit_logs WHERE project_id=:p AND action='human_review_decide' AND result='success'),
        (SELECT count(*) FROM outbox_events WHERE payload->>'projectId'=CAST(:p AS text) AND event_type='human_review_decide'),
        (SELECT count(*) FROM idempotency_keys WHERE operation='human_review_decide' AND resource_id IN (SELECT id FROM reviews WHERE project_id=:p)),
        (SELECT count(*) FROM tasks WHERE project_id=:p AND source_type='review')'''), {'p': pid}).one()


def test_charter_atomic_decision_acl_replay_and_concurrency(live, team):
    engine, api, _, _ = live
    pid, people, users, _ = team
    reviewer, second, owner, pm = (users[role] for role in ('reviewer', 'outsider', 'initiator', 'pm'))
    with engine.begin() as c:
        member(c, pid, people['outsider'], people['initiator'], 'reviewer')
        member(c, pid, people['initiator'], people['pm'], 'reviewer')
        uid = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == people['pm']))
        c.execute(db.user_roles.insert().values(user_id=uid, role='system_admin'))
    path = start_charter(pid, users)
    state = bench(reviewer, path)
    assert state['canScore'] and not state['canDecide']
    body = score_command(state, [4, 3, 3, 3])
    assert reviewer.post(path + '/scores', headers=headers(), json={**body, 'reviewerId': str(people['reviewer'])}).status_code == 422
    changed = score_command(state, [4, 3, 3, 3], source=f'charter:{uuid4()}@2')
    assert reviewer.post(path + '/scores', headers=headers(), json=changed).json()['code'] == 'REVIEW_SOURCE_NOT_FROZEN'
    wrong = {**body, 'dimensions': {k: {**v, 'score': '3'} for k, v in body['dimensions'].items()}}
    assert reviewer.post(path + '/scores', headers=headers(), json=wrong).status_code == 422
    assert owner.post(path + '/scores', headers=headers(), json=body).json()['code'] == 'INDEPENDENT_REVIEWER_REQUIRED'
    assert pm.post(path + '/scores', headers=headers(), json=body).json()['code'] == 'REVIEWER_ASSIGNMENT_REQUIRED'
    token = issue(owner, pid, actions=('pmo_review_list', 'pmo_decision_get'))
    for action, payload in [('scores', body), ('appeals', {'reason': 'Synthetic appeal'}), ('decisions', outcome(state))]:
        response = api.post(f'/api/v1/{path}/{action}', json=payload, headers={**headers(), 'Authorization': 'Bearer ' + token})
        assert (response.status_code, response.json()['code']) == (403, 'FORBIDDEN')
    key = headers(state['review']['revision'])
    first = reviewer.post(path + '/scores', headers=key, json=body)
    assert first.json() == reviewer.post(path + '/scores', headers=key, json=body).json()
    assert 'assessment' not in bench(second, path) and not bench(second, path)['scores']
    final_state = score(second, path, [4, 3, 3, 3])
    assert final_state['assessment']['total'] == '3.20'
    assert final_state['assessment']['classification'] == 'normal'
    assert len(final_state['assessment']['weakDimensions']) == 3
    assert owner.post(path + '/decisions', headers=headers(3), json=outcome(state)).json()['code'] == 'INDEPENDENT_REVIEWER_REQUIRED'
    with engine.begin() as c:
        # Unrelated work must not invalidate a pending gate.
        c.execute(db.tasks.insert().values(project_id=pid, created_by=people['pm'], title='Unrelated task',
                  source_type='manual', source_id=pid, status='open'))
        c.exec_driver_sql('CREATE TRIGGER decision_outbox_failure BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        h = headers(final_state['review']['revision'])
        command = outcome(final_state)
        failure = reviewer.post(path + '/decisions', headers=h, json=command)
        assert failure.status_code == 500
        with engine.connect() as c:
            assert result_counts(c, pid) == ('charter_review', 0, 0, 1, 0, 0, 0, 0)
    finally:
        with engine.begin() as c: c.exec_driver_sql('DROP TRIGGER decision_outbox_failure ON outbox_events')
    assert bench(reviewer, path)['review']['revision'] == final_state['review']['revision']
    other = headers(final_state['review']['revision'])
    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(lambda pair: pair[0].post(path + '/decisions', headers=pair[1], json=command), [(reviewer, h), (second, other)]))
    assert sorted(r.status_code for r in replies) == [200, 409]
    idx = next(i for i, res in enumerate(replies) if res.is_success)
    human, used = (reviewer, h) if idx == 0 else (second, other)
    first = replies[idx]
    assert human.post(path + '/decisions', headers=used, json=command).json() == first.json()
    assert human.post(path + '/decisions', headers=used, json={**command, 'rationale': 'Different reason'}).json()['code'] == 'IDEMPOTENCY_CONFLICT'
    finished = bench(human, path)
    assert finished['decision']['stage'] == 'designing' and finished['decision']['rationale'] == command['rationale']
    assert len(finished['scores']) == 2 and len(finished['tasks']) == 3
    assert all(i['assigneeId'] == str(people['pm']) and i['sourceType'] == 'review' for i in finished['tasks'])
    assert data(owner.get(f'projects/{pid}'))['project']['stage'] == 'designing'
    assert decide(human, path).json()['code'] == 'REVIEW_NOT_PENDING'
    with engine.connect() as c:
        assert result_counts(c, pid) == ('designing', 1, 1, 2, 1, 1, 1, 3)
        row = c.execute(sa.text('SELECT * FROM review_decision_snapshots WHERE request_id=:rid'), {'rid': first.headers['X-Request-ID']}).mappings().one()
        assert row.payload_hash == fingerprint(row.payload) and len(row.payload['scorecards']) == 2


def test_charter_veto_appeal_preserves_opinions_fast_confirmation(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    reviewer, second, sponsor, owner = (users[role] for role in ('reviewer', 'outsider', 'sponsor', 'initiator'))
    with engine.begin() as c: member(c, pid, people['outsider'], people['initiator'], 'reviewer')
    path = start_charter(pid, users)
    score(reviewer, path, [1, 5, 5, 5])
    bad = score(second, path, [5, 5, 5, 5])
    assert bad['assessment']['total'] == '4.60' and bad['assessment']['classification'] == 'veto'
    assert decide(second, path).json()['code'] == 'CHARTER_SCORE_VETO'
    assert decide(second, path, verdict='conditional_pass').json()['code'] == 'CHARTER_SCORE_VETO'
    appeal = {'reason': 'Review newly available interpretation of the same evidence.'}
    key = headers(bench(owner, path)['review']['revision'])
    first = owner.post(path + '/appeals', headers=key, json=appeal)
    assert data(first)['round'] == 2
    assert first.json() == owner.post(path + '/appeals', headers=key, json=appeal).json()
    assert owner.post(path + '/appeals', headers=headers(4), json=appeal).json()['code'] == 'REVIEW_APPEAL_LIMIT'
    assert decide(second, path).json()['code'] == 'CHARTER_SCORE_VETO'
    revised = score(reviewer, path, [3, 5, 5, 5])
    assert [r['round'] for r in revised['scores']] == [1, 2]
    assert revised['assessment']['total'] == '4.80'
    assert decide(reviewer, path).json()['code'] == 'SPONSOR_CONFIRMATION_REQUIRED'
    data(sponsor.post(path + '/confirmations', headers=headers(revised['review']['revision']), json={'role': 'sponsor'}))
    assert decide(reviewer, path).json()['code'] == 'INITIATOR_CONFIRMATION_REQUIRED'
    data(owner.post(path + '/confirmations', headers=headers(bench(owner, path)['review']['revision']), json={'role': 'initiator'}))
    final = data(decide(reviewer, path))
    assert final['decision']['stage'] == 'designing' and len(final['scores']) == 3
    assert len(final['confirmations']) == 2 and final['appeal']['reason'] == appeal['reason']
    assert not final['tasks']


def test_review_expired_material_requires_withdraw_and_fresh_submission(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    reviewer, owner, outsider = (users[role] for role in ('reviewer', 'initiator', 'outsider'))
    path = start_charter(pid, users)
    score(reviewer, path, [4, 4, 4, 4])
    with engine.begin() as c:
        c.execute(db.charters.update().where(db.charters.c.project_id == pid).values(
            value_prop='Externally imported material revision', revision=db.charters.c.revision+1))
    expired = bench(reviewer, path)
    assert expired['stale'] and not expired['canDecide']
    assert decide(reviewer, path).json()['code'] == 'REVIEW_SNAPSHOT_STALE'
    assert outsider.get(path + '/workbench').status_code == 404
    assert outsider.post(path + '/decisions', headers=headers(2), json=outcome(expired)).status_code == 404
    with engine.begin() as c:
        member(c, pid, people['outsider'], people['initiator'], 'reviewer')
    assert decide(outsider, path).json()['code'] == 'REVIEWER_ASSIGNMENT_REQUIRED'
    data(owner.post(path + '/withdraw', headers=headers(2), json={'reason': 'Review must include updated material.'}))
    new_path = start_charter(pid, users)
    assert new_path != path and bench(owner, path)['review']['verdict'] == 'withdrawn'
    result = score(reviewer, new_path, [4, 4, 4, 4])
    assert result['assessment']['weakDimensions'] == []
    assert data(decide(reviewer, new_path))['tasks'] == []


def test_feasibility_shortfall_and_conditional_pass_real_previous_gate(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    path = start_charter(pid, users)
    score(reviewer, path, [4, 4, 4, 4])
    data(decide(reviewer, path))
    project, _, _ = planning_setup(engine, pid, people, users, seed_stage=False)
    conditions = [{'title': 'Validate independent backup design', 'assigneeId': str(people['pm']),
                   'dueAt': (datetime.now(UTC) + timedelta(days=7)).isoformat()}]

    def submit():
        stage = data(pm.get(project))['project']
        return 'reviews/' + data(pm.post(project + '/reviews', headers=headers(stage['revision']), json={'reviewType': 'feasibility'}))['reviewId']

    shortfall = submit()
    assert score(reviewer, shortfall, [2, 5, 5, 5, 5])['assessment']['classification'] == 'shortfall'
    assert decide(reviewer, shortfall).json()['code'] == 'FEASIBILITY_SHORTFALL'
    assert decide(reviewer, shortfall, verdict='conditional_pass', conditions=conditions).json()['code'] == 'FEASIBILITY_SHORTFALL'
    still_pending = bench(reviewer, shortfall)
    assert still_pending['review']['status'] == 'pending' and 'decision' not in still_pending and len(still_pending['tasks']) == 1
    returned = data(decide(reviewer, shortfall, verdict='redirect'))
    assert returned['decision']['stage'] == 'designing' and len(returned['tasks']) == 1
    ready = submit()
    score(reviewer, ready, [3, 3, 3, 3, 3])
    assert decide(reviewer, ready, verdict='conditional_pass').json()['code'] == 'CONDITIONS_REQUIRED_OR_NOT_ALLOWED'
    executed = data(decide(reviewer, ready, verdict='conditional_pass', conditions=conditions * 2))
    assert executed['decision']['stage'] == 'executing' and len(executed['tasks']) == 1
    assert executed['tasks'][0]['dueAt'] and executed['tasks'][0]['assigneeId'] == str(people['pm'])


def test_gate_compares_exact_values_before_display_rounding():
    def card(values, keys=WEIGHTS):
        return SimpleNamespace(dimensions={k: {'score': v} for k, v in zip(keys, values, strict=True)})
    for base, edge, classify_below, classify_at in [(3, '3.20', 'below_threshold', 'normal'), (4, '4.20', 'normal', 'fast_track')]:
        baseline = card([base] * 4)
        above = card([base+1, base, base, base])
        almost = assess('charter', [above] * 99 + [baseline])
        exact = assess('charter', [above])
        assert (almost.total, almost.classification) == (edge, classify_below)
        assert (exact.total, exact.classification) == (edge, classify_at)
    strong = card([5] * 5, FeasibilityDimensions.model_fields)
    weak = card([2, 5, 5, 5, 5], FeasibilityDimensions.model_fields)
    assert assess('feasibility', [strong, weak]).classification == 'shortfall'
