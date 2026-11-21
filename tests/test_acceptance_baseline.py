"""Additional product cases from the frozen A01-A16 acceptance examples."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from test_health import code, prepare, sample
from test_lifecycle import stage, upload
from test_reference_vectors_closing import assert_closing
from test_reference_vectors_governance import verify_shortfall
from test_reference_vectors_permissions import bearer, denied_audit, mcp_calls
from test_reference_vectors_signals import events
from test_requirement_exceptions import deliver_with_exception_material
from test_reviews import bench, data, decide, headers, issue, outcome, result_counts, score, start_charter, team as team


def test_a05_verified_actual_and_remaining_at_exactly_twenty_percent(live, team):
    _, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    project, _ = prepare(live, team)
    with live[0].begin() as c:
        verifier = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == people['reviewer']))
        c.execute(db.user_roles.insert().values(user_id=verifier, role='finance_verifier'))
    saved = data(pm.post(project + '/cost-postings', headers=headers(), json={
        'type': 'money', 'amount': '70', 'period': '2025-01', 'sourceRef': 'Fixed A05 purchase'}))
    data(reviewer.post(f"cost-postings/{saved['postingId']}/verify", headers=headers(), json={
        'reason': 'Independently checked the fixed synthetic purchase receipt.', 'occurredAt': '2025-01-10T08:00:00Z'}))
    plan = data(pm.get(project + '/costs'))['plan']
    data(pm.put(project + '/cost-forecasts', headers=headers(plan['revision']), json={
        'type': 'money', 'forecastRemaining': '50', 'reason': 'Remaining work from the fixed synthetic estimate.'}))
    cost = data(pm.get(project + '/costs'))['money']
    assert (cost['budget'], cost['actual'], cost['forecastRemaining'], cost['projectedTotal']) == (
        '100.00', '70.00', '50.00', '120.00')
    health = sample(pm, project)
    assert health['costLine'] == 'red' and health['moneyVarianceRate'] == '0.2000'
    line = health['inputs']['cost']['money']
    assert line['overspend'] is True and line['reassess'] is False
    alert, = events(pm, project, 'LR-04')
    assert alert['severity'] == 'red' and f"cost-posting:{saved['postingId']}" in alert['sourceRefs']
    assert not [task for task in data(pm.get(project + '/tasks'))['items'] if task['title'].startswith('LR-04')]


@pytest.mark.parametrize('points', [pytest.param(5, id='A01'), pytest.param(4, id='A02')])
def test_charter_assessment_never_replaces_decision(points, live, team):
    pid, _, users, _ = team
    applicant, reviewer, sponsor = (users[who] for who in ('initiator', 'reviewer', 'sponsor'))
    review = start_charter(pid, users)
    assessment = score(reviewer, review, [points] * 4)
    assert assessment['assessment']['total'] == f'{points:.2f}'
    assert assessment['assessment']['classification'] == ('fast_track' if points == 5 else 'normal')
    assert stage(applicant, f'projects/{pid}')['stage'] == 'charter_review'
    if points == 5:
        code(decide(reviewer, review), 422, 'SPONSOR_CONFIRMATION_REQUIRED')
        next_state = data(sponsor.post(review + '/confirmations', headers=headers(assessment['review']['revision']),
            json={'role': 'sponsor'}))
        code(decide(reviewer, review), 422, 'INITIATOR_CONFIRMATION_REQUIRED')
        data(applicant.post(review + '/confirmations', headers=headers(next_state['review']['revision']), json={'role': 'initiator'}))
        assert stage(applicant, f'projects/{pid}')['stage'] == 'charter_review'
    result = data(decide(reviewer, review))
    assert result['decision']['stage'] == 'designing' and result['decision']['rationale'] and not result['tasks']


def test_a04_feasibility_shortfall(live, team):
    verify_shortfall(live, team, [5, 5, 2, 5, 5], 'pass', True)


@pytest.mark.parametrize('exception', [pytest.param(True, id='A07'), pytest.param(False, id='A08')])
def test_independent_lr07_refusals(exception, live, team):
    _, _, users, _ = team
    project, acceptance = deliver_with_exception_material(live, team, confirm=not exception,
        close_risk=exception, actuals=('125.00' if exception else '100.00', '10.00'))
    if exception:
        state, command = acceptance()
        data(users['client_representative'].post(project + '/requirement-exceptions', headers=headers(state['revision']), json=command))
    assert_closing(live, team, project, {'MONEY_CLOSING_VARIANCE' if exception else 'BLOCKING_RISK_UNRESOLVED'})


def test_a11_agent_cannot_read_unscoped_project_or_original(live, team):
    pid, _, users, _ = team
    pm, reviewer, owner, outsider = (users[who] for who in ('pm', 'reviewer', 'initiator', 'outsider'))
    path = f'projects/{pid}'
    milestone = data(pm.post(path + '/milestones', headers=headers(), json={
        'name': 'Private synthetic milestone', 'plannedDate': '2026-12-10', 'isKey': True}))
    proof = upload(pm, reviewer, path, 'milestone', milestone['milestoneId'])
    proof_path = f"evidence/{proof['evidenceId']}"
    original = pm.get(proof_path, params={'download': True})
    assert original.is_success and sha256(original.content).hexdigest() == proof['sha256']
    permitted = data(owner.post('projects', headers=headers(), json={'code': 'SCOPE-A11', 'name': 'Only authorized project'}))
    token = issue(owner, permitted['projectId'], ['pmo_project_get'])
    assert data(live[1].get(f"/api/v1/projects/{permitted['projectId']}", headers=bearer(token)))['project'] == permitted
    for resource, status, error, op in ((path, 404, 'RESOURCE_NOT_FOUND', 'pmo_project_get'),
                                       (proof_path, 403, 'FORBIDDEN', 'human_evidence_get')):
        denied = live[1].get('/api/v1/' + resource, headers=bearer(token), params={'download': True} if resource == proof_path else {})
        code(denied, status, error)
        assert set(denied.json()) == {'code', 'message', 'requestId'}
        assert 'content-disposition' not in denied.headers and proof['sha256'] not in denied.text
        denied_audit(live[0], denied, op)
        code(outsider.get(resource), 404, 'RESOURCE_NOT_FOUND')


def test_a12_only_current_token_inherits_new_draft(live, team):
    pid, people, users, _ = team
    owner = users['initiator']
    actions = ['pmo_project_create', 'pmo_project_draft_update', 'pmo_project_get']

    def mint():
        rev = data(owner.get('me/agent-tokens'))['collectionRevision']
        issued = owner.post('me/agent-tokens', headers=headers(rev), json={'allowedActions': actions,
            'allowedProjectIds': [], 'allowProjectCreate': True, 'expiresAt': (datetime.now(UTC) + timedelta(minutes=20)).isoformat()})
        return data(issued)['tokenId'], issued.headers['X-PMO-New-Token']

    first, token = mint()
    second, other = mint()
    created, = mcp_calls(live, token, [('pmo_project_create', {'code': 'DRAFT-A12', 'name': 'Created by delegated token',
        'idempotencyKey': uuid4().hex})])
    assert not created.isError
    row = created.structuredContent['data']
    commands = [('pmo_project_draft_update', {'projectId': row['projectId'], 'expectedRevision': row['revision'],
        'fields': {'name': 'Revised by the same token'}, 'idempotencyKey': uuid4().hex}), ('pmo_project_get', {'projectId': pid})]
    saved, denied = mcp_calls(live, token, commands)
    assert not saved.isError and saved.structuredContent['data']['revision'] == 2
    assert saved.structuredContent['data']['stage'] == 'drafting'
    assert denied.isError and denied.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND'
    other_read, = mcp_calls(live, other, [('pmo_project_get', {'projectId': row['projectId']})])
    assert other_read.isError and other_read.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND'
    with live[0].connect() as c:
        t = db.agent_credentials
        assert c.scalar(sa.select(t.c.allowed_project_ids).where(t.c.id == first)) == [row['projectId']]
        assert c.scalar(sa.select(t.c.allowed_project_ids).where(t.c.id == second)) == []
        assert c.execute(sa.select(db.project_members.c.role, db.project_members.c.person_id)
            .where(db.project_members.c.project_id == row['projectId'])).one() == ('initiator', people['initiator'])


def test_a14_audit_failure_rolls_back_decision_and_state(live, team):
    pid, _, users, _ = team
    reviewer = users['reviewer']
    engine = live[0]
    review = start_charter(pid, users)
    ready = score(reviewer, review, [4, 3, 3, 3])
    key = headers(ready['review']['revision'])
    payload = outcome(ready)
    with engine.begin() as c:
        before = result_counts(c, pid)
        c.exec_driver_sql('CREATE TRIGGER acceptance_audit_failure BEFORE INSERT ON audit_logs FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        code(reviewer.post(review + '/decisions', headers=key, json=payload), 500, 'DATABASE_FAILURE')
        with engine.connect() as c:
            assert result_counts(c, pid) == before
        assert bench(reviewer, review)['review']['revision'] == ready['review']['revision']
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER acceptance_audit_failure ON audit_logs')
    result = reviewer.post(review + '/decisions', headers=key, json=payload)
    assert data(result)['decision']['stage'] == 'designing'
    assert reviewer.post(review + '/decisions', headers=key, json=payload).json() == result.json()
