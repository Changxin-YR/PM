"""Frozen authority cases on the HTTP boundary and the actual stdio adapter."""
import asyncio
from collections.abc import Iterable
import hashlib
import sys
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from test_health import code
from test_lifecycle import stage
from test_reference_vectors import cases
from test_reviews import CHARTER, bench, data, decide, headers, issue, member, outcome, score, start_charter, team as team


def mcp_calls(live, token, commands: Iterable[tuple[str, dict]]):
    async def invoke():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'pmo.mcp_server'], env={
            'PMO_AGENT_TOKEN': token, 'PMO_API_URL': f'http://127.0.0.1:{live[2]}/api/v1', 'PYTHONUTF8': '1'})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            return [await session.call_tool(name, payload) for name, payload in commands]

    return asyncio.run(invoke())


def denied_audit(engine, response, operation):
    with engine.connect() as c:
        row = c.execute(sa.select(db.audit_logs).where(db.audit_logs.c.request_id == response.headers['X-Request-ID'])).one()
        assert (row.action, row.result, row.error_code) == (operation, 'denied', response.json()['code'])


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.mark.parametrize('case', cases('approval', lambda c: c['action'] == 'approve_gate'))
def test_current_independent_gate_authority(case, live, team):
    engine, api, _, _ = live
    pid, people, users, _ = team
    reviewer, applicant, pm = (users[i] for i in ('reviewer', 'initiator', 'pm'))
    if case.get('sameAsApplicant'):
        with engine.begin() as c:
            member(c, pid, people['initiator'], people['pm'], 'reviewer')
    path = start_charter(pid, users)
    state = score(reviewer, path, [4] * 4)
    rev = state['review']['revision']
    expected = case['expected']
    if case['actorType'] == 'agent':
        assert case['scopePresent']
        token = issue(pm, pid, ['pmo_review_list', 'pmo_decision_get'])
        allowed = data(api.get(f'/api/v1/projects/{pid}/reviews', headers=bearer(token)))
        assert [row['reviewId'] for row in allowed['items']] == [state['review']['reviewId']]
        response = api.post('/api/v1/' + path + '/decisions', headers={**headers(rev), **bearer(token)}, json=outcome(state))
        code(response, 403, expected)
    elif case.get('sameAsApplicant'):
        response = applicant.post(path + '/decisions', headers=headers(rev), json=outcome(state))
        code(response, 403, 'INDEPENDENT_REVIEWER_REQUIRED')
    elif case['requestHashMatches']:
        assert expected == 'ALLOW_IF_BUSINESS_GATE_PASS' and state['canDecide']
        assert data(decide(reviewer, path))['decision']['stage'] == 'designing'
        return
    else:
        assert expected == 'APPROVAL_STALE'
        with engine.begin() as c:
            c.execute(db.charters.update().where(db.charters.c.project_id == pid).values(
                revision=db.charters.c.revision + 1, value_prop='Newly imported scope facts.'))
        assert bench(reviewer, path)['stale']
        response = reviewer.post(path + '/decisions', headers=headers(rev), json=outcome(state))
        code(response, 409, 'REVIEW_SNAPSHOT_STALE')
    denied_audit(engine, response, 'human_review_decide')
    assert stage(pm, f'projects/{pid}')['stage'] == 'charter_review'
    assert 'decision' not in bench(reviewer, path)


@pytest.mark.parametrize('case', cases('approval', lambda c: c['action'] == 'edit_draft'))
def test_human_draft_ownership(case, live, team):
    pid, people, users, _ = team
    who = 'initiator' if case['isDraftCreator'] else 'outsider'
    with live[0].begin() as c:
        member(c, pid, people[who], people['pm'], case['role'])
    client = users[who]
    path = f'projects/{pid}'
    first = stage(client, path)
    assert first['stage'] == case['projectStage']
    response = client.patch(path, headers=headers(first['revision']), json={'fields': {'name': 'Owned draft revision'}})
    if case['expected'] == 'ALLOW':
        assert data(response)['revision'] == first['revision'] + 1
    else:
        code(response, 403, case['expected'])
        assert stage(client, path) == first


def invite_sponsor(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    owner, office = users['initiator'], users['reviewer']
    path = f'projects/{pid}'
    draft = stage(owner, path)
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(sponsor_id=None))
        c.execute(db.project_members.update().where(db.project_members.c.project_id == pid,
            db.project_members.c.role == 'sponsor').values(revoked_at=sa.func.now()))
        c.execute(sa.text("INSERT INTO appointment_authorities(org_id,person_id,role,authority_ref,expires_at) "
            "VALUES(:org,:person,'sponsor','Synthetic invitation fixture',now()+interval '1 hour')"),
            {'org': draft['orgId'], 'person': people['reviewer']})
    charter = data(owner.get(path + '/charter'))
    data(owner.put(path + '/charter', headers=headers(charter['expectedRevision']), json={'fieldsCharter': CHARTER}))
    nominated = data(owner.post(path + '/role-invitations', headers=headers(draft['revision']),
        json={'personId': str(people['outsider']), 'role': 'sponsor'}))
    invitation = f"role-invitations/{nominated['invitationId']}"
    assert data(office.post(invitation + '/issue', headers=headers()))['status'] == 'invited'
    pending, = data(users['outsider'].get('me/role-invitations'))
    return path, invitation, pending


@pytest.mark.parametrize('case', cases('approval', lambda c: 'role_assignment' in c['action'] or c['action'] == 'submit_charter_review'))
def test_nomination_does_not_grant_authority(case, live, team):
    pid, _, users, _ = team
    owner = users['initiator']
    path, invitation, pending = invite_sponsor(live, team)
    if case['actorType'] == 'agent':
        token = issue(owner, pid, ['pmo_role_assignment_propose'])
        response = live[1].post('/api/v1/' + invitation + '/accept', headers={**headers(pending['revision']), **bearer(token)})
        code(response, 403, case['expected'])
        assert data(users['outsider'].get('me/role-invitations')) == [pending]
    else:
        assert case['assignmentStatus'] == 'pending' and pending['status'] == 'invited'
        code(owner.post(path + '/reviews', headers=headers(stage(owner, path)['revision']), json={'reviewType': 'charter'}),
             422, 'GATE_REQUIREMENTS_NOT_MET')
        assert 'SPONSOR_ASSIGNMENT_REQUIRED' in {g['code'] for g in data(owner.get(path + '/gates/check', params={'gate': 'charter'}))['missing']}
    with live[0].connect() as c:
        assert c.scalar(sa.select(db.projects.c.sponsor_id).where(db.projects.c.id == pid)) is None


def deny_agent_verification(live, team, actions, expected):
    pid, _, users, _ = team
    pm = users['pm']
    token = issue(pm, pid, actions)
    path = f'projects/{pid}'
    posting = data(live[1].post(f'/api/v1/{path}/cost-postings', headers={**headers(), **bearer(token)}, json={
        'type': 'money', 'amount': '25.00', 'period': '2025-01', 'sourceRef': 'SYNTHETIC-PROPOSAL'}))
    response = live[1].post(f"/api/v1/cost-postings/{posting['postingId']}/verify", headers={**headers(), **bearer(token)},
        json={'reason': 'Attempt through direct REST.', 'occurredAt': '2025-01-03T00:00:00Z'})
    code(response, 403, expected)
    denied_audit(live[0], response, 'human_verify_cost_posting')
    assert data(pm.get(path + '/costs'))['money']['actual'] == '0.00'
    assert data(pm.get(path + '/cost-postings'))['items'][0]['status'] == 'pending'


@pytest.mark.parametrize('case', cases('approval', lambda c: c['action'] == 'cost_posting_verify'))
def test_posting_approval_is_human_only(case, live, team):
    assert case['actorType'] == 'agent'
    deny_agent_verification(live, team, ['pmo_cost_posting_propose'], case['expected'])


@pytest.mark.parametrize('case', cases('approval', lambda c: c['action'] == 'transition_project'))
def test_illegal_transition_has_no_success_receipt(case, live, team):
    pid, _, users, _ = team
    pm = users['pm']
    path = f'projects/{pid}'
    assert not case['businessRuleAllows']
    response = pm.post(path + '/transition-requests', headers=headers(stage(pm, path)['revision']),
        json={'toStage': 'executing', 'reason': 'Attempting an invalid transition.'})
    code(response, 422, 'TRANSITION_NOT_ALLOWED')
    denied_audit(live[0], response, 'pmo_transition_request')
    with live[0].connect() as c:
        assert c.scalar(sa.select(db.outbox_events.c.id).where(db.outbox_events.c.dedupe_key == response.headers['X-Request-ID'])) is None
    assert stage(pm, path)['stage'] == 'drafting'


@pytest.mark.parametrize('case', cases('mcpPermissions', lambda c: 'tokenProjectIds' in c or c.get('tokenExpired')))
def test_token_lifetime_and_project_scope(case, live, team):
    pid, _, users, _ = team
    owner = users['initiator']
    actions = case.get('tokenActions', ['pmo_project_get'])
    token = issue(owner, pid, actions)
    if case.get('tokenExpired'):
        with live[0].begin() as c:
            c.execute(db.agent_credentials.update().where(db.agent_credentials.c.token_hash == hashlib.sha256(token.encode()).hexdigest())
                .values(expires_at=sa.func.now() - sa.text("interval '1 second'")))
        response = live[1].get(f'/api/v1/projects/{pid}', headers=bearer(token))
        code(response, 401, case['expected'])
        result, = mcp_calls(live, token, [('pmo_project_get', {'projectId': pid})])
        assert result.isError and result.structuredContent['error']['code'] == case['expected']
    else:
        assert case['tokenProjectIds'] == ['p1'] and case['requestedProjectId'] == 'p2'
        other = data(owner.post('projects', headers=headers(), json={'code': 'UNSCOPED', 'name': 'Same owner outside token scope'}))
        assert data(owner.get(f"projects/{other['projectId']}"))['project'] == other
        result, forbidden = mcp_calls(live, token, [('pmo_project_get', {'projectId': target})
            for target in (pid, other['projectId'])])
        assert not result.isError and result.structuredContent['data']['project']['projectId'] == pid
        assert forbidden.isError and forbidden.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND'
        response = live[1].get(f"/api/v1/projects/{other['projectId']}", headers=bearer(token))
        code(response, 404, 'RESOURCE_NOT_FOUND')
        denied_audit(live[0], response, 'pmo_project_get')


@pytest.mark.parametrize('case', cases('mcpPermissions', lambda c: c.get('projectCreateRequested')))
def test_create_token_scope_is_not_a_business_role(case, live, actor):
    engine = live[0]
    token, credential = actor(case['allowProjectCreate'])
    with engine.begin() as c:
        t = db.agent_credentials
        account = c.scalar(sa.select(t.c.delegated_by).where(t.c.id == credential))
        c.execute(t.update().where(t.c.id == credential).values(allowed_actions=case['tokenActions']))
        if case['tokenOrgId'] != case['delegatorOrgId']:
            other = c.scalar(db.organizations.insert().values(name='Other synthetic org', status='active').returning(db.organizations.c.id))
            c.execute(t.update().where(t.c.id == credential).values(org_id=other))
        if not case['delegatorActiveInOrg']:
            c.execute(db.persons.update().where(db.persons.c.user_id == account).values(is_active=False))
    command = {'code': 'SCOPE-VECTOR', 'name': 'Org creation vector', 'idempotencyKey': uuid4().hex}
    result, = mcp_calls(live, token, [(case['operation'], command)])
    if case['expected'] == 'ALLOW_DRAFT_ONLY':
        assert not result.isError
        project = result.structuredContent['data']
        assert project['stage'] == 'drafting' and project['revision'] == 1
        with engine.connect() as c:
            assert c.scalar(sa.select(t.c.allowed_project_ids).where(t.c.id == credential)) == [project['projectId']]
            assert c.scalars(sa.select(db.project_members.c.role).where(db.project_members.c.project_id == project['projectId'])).all() == ['initiator']
    else:
        expected_code = 'UNAUTHORIZED' if case['allowProjectCreate'] else 'FORBIDDEN'
        assert result.isError and result.structuredContent['error']['code'] == expected_code
        with engine.connect() as c:
            p = db.projects
            person = c.scalar(sa.select(db.persons.c.id).where(db.persons.c.user_id == account))
            assert c.scalar(sa.select(p.c.id).where(p.c.initiator_id == person)) is None
            assert c.scalar(sa.select(t.c.allowed_project_ids).where(t.c.id == credential)) == []


@pytest.mark.parametrize('case', cases('mcpPermissions', lambda c: 'tokenActions' in c and 'tokenProjectIds' not in c and not c.get('projectCreateRequested')))
def test_scoped_drafts_cannot_become_approvals(case, live, team):
    pid, _, users, _ = team
    actions = case['tokenActions']
    if case.get('operation') == 'verify_cost_posting':
        deny_agent_verification(live, team, actions, case['expected'])
        return
    owner = users['initiator']
    project = f'projects/{pid}'
    if case.get('operation') == 'accept_role_assignment':
        _, path, pending = invite_sponsor(live, team)
        token = issue(owner, pid, actions)
        response = live[1].post('/api/v1/' + path + '/accept', headers={**headers(pending['revision']), **bearer(token)})
        code(response, 403, case['expected'])
        return
    token = issue(owner, pid, actions)
    tool, = actions
    if case.get('isDraftCreator'):
        result, = mcp_calls(live, token, [(tool, {'projectId': pid, 'expectedRevision': stage(owner, project)['revision'],
            'idempotencyKey': uuid4().hex, 'fields': {'name': 'Agent edited its delegated draft'}})])
        assert not result.isError
        assert result.structuredContent['data'] == stage(owner, project)
        assert stage(owner, project)['stage'] == 'drafting'
    else:
        result, = mcp_calls(live, token, [(tool, {'projectId': pid, 'idempotencyKey': uuid4().hex,
            'priority': 'P0', 'desc': 'A scoped candidate requirement.', 'acceptanceCriteria': 'Acceptance is not automatic.'})])
        assert not result.isError
        req = result.structuredContent['data']
        assert req['status'] == 'baseline' and req['baselineVersion'] == 0
        if case.get('operation') == 'customer_confirm':
            response = live[1].post(f"/api/v1/requirements/{req['requirementId']}/customer-confirm",
                headers={**headers(req['revision']), **bearer(token)}, json={'evidenceRef': f'evidence:{uuid4()}'})
            code(response, 403, case['expected'])
            denied_audit(live[0], response, 'human_customer_confirm')
        assert data(owner.get(project + '/requirements'))['items'][0]['baselineVersion'] == 0
