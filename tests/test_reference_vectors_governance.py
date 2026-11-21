"""Frozen transition, shortfall, and requirements cases through the real review services."""
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from uuid import UUID

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from test_health import code
from test_lifecycle import begin_execution, complete_milestones, confirm_requirement, execute, gate, propose, stage, upload
from test_reference_vectors import cases
from test_reference_vectors_permissions import mcp_calls
from test_requirement_additions import grant_reviewers
from test_reviews import data, decide, headers, issue, planning_setup, result_counts, score, start_charter, team as team


def lr(rule):
    return cases('lrRules', lambda case: case['rule'] == rule)


@pytest.mark.parametrize('case', lr('LR-01'))
def test_transition_requires_decision(case, live, team):
    pid, _, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    path = f'projects/{pid}'
    review = start_charter(pid, users) if case['fromState'] == 'charter_review' else None
    assert stage(pm, path)['stage'] == case['fromState']
    if case['expectedTriggered']:
        with live[0].connect() as c:
            before = result_counts(c, pid)
        denied = propose(pm, path, case['toState'])
        code(denied, 422, 'TRANSITION_NOT_ALLOWED')
        current = stage(pm, path)
        forged = pm.patch(path, headers=headers(current['revision']), json={'fields': {'stage': case['toState']}})
        code(forged, 422, 'VALIDATION_FAILED')
        op = 'pmo_transition_request'
        collection = data(pm.get('me/agent-tokens'))
        grant = pm.post('me/agent-tokens', headers=headers(collection['collectionRevision']), json={
            'allowedActions': [op], 'allowedProjectIds': [pid],
            'expiresAt': (datetime.now(UTC) + timedelta(minutes=20)).isoformat()})
        code(grant, 403, 'FORBIDDEN')
        assert data(pm.get('me/agent-tokens')) == collection
        token = issue(pm, pid, ['pmo_project_get'])
        allowed, result = mcp_calls(live, token, [('pmo_project_get', {'projectId': pid}), (op, {
            'projectId': pid, 'toStage': case['toState'],
            'reason': 'A scoped synthetic request cannot bypass the gate.', 'expectedRevision': current['revision'],
            'idempotencyKey': headers()['Idempotency-Key']})])
        assert not allowed.isError and allowed.structuredContent['data']['project'] == current
        assert result.isError and result.structuredContent['error']['code'] == 'FORBIDDEN'
        assert stage(pm, path) == current
        with live[0].connect() as c:
            assert result_counts(c, pid) == before
            assert c.scalar(sa.select(db.audit_logs.c.result).where(
                db.audit_logs.c.request_id == denied.headers['X-Request-ID'])) == 'denied'
    else:
        assert review is not None and case['evidenceValid']
        score(reviewer, review, [4] * 4)
        assert data(decide(reviewer, review, verdict=case['gateDecision']))['decision']['stage'] == case['toState']
        assert stage(pm, path)['stage'] == case['toState']


def feasibility(live, team):
    pid, people, users, _ = team
    reviewer = users['reviewer']
    charter = start_charter(pid, users)
    score(reviewer, charter, [4] * 4)
    data(decide(reviewer, charter))
    path, _, _ = planning_setup(live[0], pid, people, users, seed_stage=False)
    return path, gate(users['pm'], path, 'feasibility')


def verify_shortfall(live, team, values, verdict, expected):
    pid, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    path, review = feasibility(live, team)
    score(reviewer, review, values)
    if not expected:
        assert data(decide(reviewer, review, verdict=verdict))['decision']['stage'] == 'executing'
        return
    with live[0].connect() as c:
        before = result_counts(c, pid)
    for _ in range(2):
        code(decide(reviewer, review, verdict=verdict), 422, 'FEASIBILITY_SHORTFALL')
    with live[0].connect() as c:
        after = result_counts(c, pid)
        assert after == (*before[:-1], before[-1] + 1)
        task = c.execute(sa.select(db.tasks).where(db.tasks.c.project_id == pid, db.tasks.c.source_type == 'review')).one()
        assert (task.assignee_id, task.source_id) == (people['pm'], UUID(review.split('/')[-1]))
        assert task.due_at is None  # Unlike conditional approval, a denial does not commit a deadline (ADR 0010).
    assert stage(pm, path)['stage'] == 'feasibility_review'


@pytest.mark.parametrize('case', lr('LR-02'))
def test_feasibility_shortfall(case, live, team):
    verify_shortfall(live, team, case['feasibilityScores'], case['requestedVerdict'], case['expectedTriggered'])


def cancel(pm, project, req):
    return data(pm.post(project + '/changes', headers=headers(req['revision']), json={
        'targetType': 'requirement', 'targetId': req['requirementId'], 'before': {'status': req['status']},
        'after': {'status': 'cancelled'}, 'reason': 'Propose deselection from the frozen scope.'}))


def apply_cancellation(reviewer, change):
    return data(reviewer.post(f"changes/{change['changeId']}/approve", headers=headers(change['revision']),
                              json={'rationale': 'Reviewed the independent cancellation request.'}))


def requirement_at(pm, path, requirement):
    return next(req for req in data(pm.get(path + '/requirements'))['items'] if req['requirementId'] == requirement)


@pytest.mark.parametrize('case', lr('LR-05'))
def test_requirement_change_requires_applied_receipt(case, live, team):
    _, _, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    assert case['requirementFrozen'] and case['scopeChangeRequested']
    project, requirement, _ = begin_execution(live, team, extra_priorities=('P1',))
    req = requirement_at(pm, project, requirement)
    assert req['baselineVersion'] == 1
    path = f'requirements/{requirement}'
    change = cancel(pm, project, req) if case['approvedChangeId'] else None
    if case['expectedTriggered']:
        code(pm.patch(path, headers=headers(req['revision']), json={'fieldsRequirement': {'priority': 'P2'}}),
             422, 'BASELINE_FROZEN')
        assert requirement_at(pm, project, requirement) == req
        if change:
            assert change['status'] == case['approvedChangeStatus']
            proof = upload(pm, reviewer, project, 'requirement', requirement)
            code(pm.post(path + '/deliver', headers=headers(req['revision']), json={'evidenceRef': proof['evidenceRef']}),
                 422, 'PENDING_SCOPE_CHANGE')
        assert data(pm.get(project + '/requirement-metrics'))['activeWeight'] == 5
    else:
        grant_reviewers(live, team)
        applied = apply_cancellation(reviewer, change)
        assert applied['status'] == 'applied'
        history = data(pm.get(path + '/changes'))['items']
        assert [row['changeId'] for row in history] == [applied['changeId']]
        assert history[0]['before'] == change['before'] and history[0]['after'] == change['after']
        assert requirement_at(pm, project, requirement)['status'] == 'cancelled'
        assert data(pm.get(project + '/requirement-metrics'))['activeWeight'] == 2


@pytest.mark.parametrize('case', cases('requirements', lambda c: 'expectedFraction' in c))
def test_scope_keeps_frozen_weight_until_approved(case, live, team):
    _, _, users, _ = team
    pm, reviewer, customer = (users[i] for i in ('pm', 'reviewer', 'client_representative'))
    first, *extra = [item['priority'] for item in case['items']]
    assert first == 'P0'
    project, _, _ = begin_execution(live, team, extra_priorities=extra)
    if any(item.get('approvedCancelled') for item in case['items']):
        grant_reviewers(live, team)
    rows = sorted(data(pm.get(project + '/requirements'))['items'], key=lambda row: row['desc'])
    for row, item in zip(rows, case['items'], strict=True):
        assert row['priority'] == item['priority'] and row['baselineVersion'] == 1
        if item['confirmed']:
            confirm_requirement(pm, reviewer, customer, project, row)
        if item.get('attemptedCancelled') or item.get('approvedCancelled'):
            change = cancel(pm, project, row)
            if item.get('approvedCancelled'):
                assert apply_cancellation(reviewer, change)['status'] == 'applied'
                assert requirement_at(pm, project, row['requirementId'])['status'] == 'cancelled'
            else:
                assert change['status'] == 'pending' and requirement_at(pm, project, row['requirementId']) == row

    quality = data(pm.get(project + '/requirement-metrics'))
    numerator, denominator = map(int, case['expectedFraction'].split('/'))
    assert (quality['confirmedWeight'], quality['activeWeight']) == (numerator, denominator)
    assert abs(Fraction(quality['satisfaction']) - Fraction(numerator, denominator)) < Fraction(1, 10**6)
    if case.get('expectedFrozen'):
        assert quality['frozenCount'] == 1 and quality['approvedChangeCount'] == 0
    if 'expectedRequirementGateEligible' in case:
        complete_milestones(pm, reviewer, project)
        data(execute(pm, project, data(propose(pm, project, 'delivering'))))
        gaps = {gap['code'] for gap in data(pm.get(project + '/closing/check'))['missing']}
        assert ('CUSTOMER_ACCEPTANCE_REQUIRED' not in gaps) is case['expectedRequirementGateEligible']
