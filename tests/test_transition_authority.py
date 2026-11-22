"""Formal authority is scoped to a natural person, a tenant, and one stage edge."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.projects import fingerprint
from test_database_roles import serve
from test_lifecycle import begin_execution, judgment, propose, stage, start_feasibility
from test_reviews import bench, data, headers, issue, member, team as team


def grant(c, project, person, edge, *, source='SYNTHETIC-AUTH-12', starts=None, expires=None):
    now = datetime.now(UTC)
    return c.scalar(sa.text('''INSERT INTO project_transition_grants
        (org_id, project_id, person_id, from_stage, to_stage, source_ref, valid_from, expires_at)
        SELECT org_id, id, :person, :origin, :dest, :source, :starts, :expires FROM projects WHERE id=:pid
        RETURNING id'''), dict(pid=project, person=person, origin=edge[0], dest=edge[1], source=source,
            starts=starts or now - timedelta(minutes=5), expires=expires or now + timedelta(hours=1)))


def revoke(c, gid):
    c.execute(sa.text('''UPDATE project_transition_grants SET revoked_at=clock_timestamp(), revocation_ref='SYNTHETIC-REVOKE-1'
        WHERE id=:id'''), {'id': gid})


def hold_until():
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat()


def item(client, request):
    return next(row for row in data(client.get('approvals', params={'projectId': request['projectId']}))['items']
                if row['requestId'] == request['requestId'])


def effects(c, request):
    return c.execute(sa.text('''SELECT
        (SELECT count(*) FROM project_transition_outcomes WHERE approval_id=:id),
        (SELECT count(*) FROM decisions d JOIN approval_requests q ON q.project_id=d.project_id
           WHERE q.id=:id AND d.subject LIKE 'project-transition:%'),
        (SELECT count(*) FROM project_stage_transitions WHERE approval_request_id=:id),
        (SELECT count(*) FROM audit_logs a JOIN approval_requests q ON q.project_id=a.project_id
           WHERE q.id=:id AND a.action='human_decide_approval' AND a.result='success'),
        (SELECT count(*) FROM outbox_events WHERE event_type='human_decide_approval' AND payload->>'approvalRequestId'=CAST(:id AS text)),
        (SELECT count(*) FROM idempotency_keys WHERE operation='human_decide_approval' AND resource_id=:id)'''),
        {'id': UUID(request['requestId'])}).one()


def test_non_sponsor_can_approve_only_the_current_authorized_edge(live, team, tmp_path):
    engine, api, _, _ = live
    pid, people, users, _ = team
    pm, delegate, admin = (users[key] for key in ('pm', 'outsider', 'reviewer'))
    project, _, _ = begin_execution(live, team)
    pause = data(propose(pm, project, 'on_hold'))
    edge = ('executing', 'on_hold')
    now = datetime.now(UTC)
    other = data(users['initiator'].post('projects', headers=headers(), json={'code': 'OTHER', 'name': 'Other project'}))['projectId']
    admin_id = admin.get('auth/me').json()['accountId']
    with engine.begin() as c:
        membership = member(c, pid, people['outsider'], people['initiator'], 'member')
        c.execute(db.user_roles.insert().values(user_id=admin_id, role='system_admin'))
        grant(c, other, people['outsider'], edge)
        grant(c, pid, people['outsider'], ('on_hold', 'executing'))
        grant(c, pid, people['outsider'], edge, starts=now-timedelta(hours=2), expires=now-timedelta(hours=1))
        grant(c, pid, people['outsider'], edge, starts=now+timedelta(hours=2), expires=now+timedelta(hours=3))
        revoke(c, grant(c, pid, people['outsider'], edge))
        grant(c, pid, people['pm'], edge)
    for no in (delegate, admin, pm):
        assert not item(no, pause)['canDecide']
        denied = judgment(no, pause, holdUntil=hold_until())
        assert (denied.status_code, denied.json()['code']) == (403, 'INDEPENDENT_APPROVER_REQUIRED')
    with engine.begin() as c:
        assert effects(c, pause) == (0, 0, 0, 0, 0, 0)
        gid = grant(c, pid, people['outsider'], edge)
    assert item(delegate, pause)['canDecide']

    with engine.begin() as c:
        c.execute(db.project_members.update().where(db.project_members.c.id == membership).values(revoked_at=sa.func.now()))
    assert judgment(delegate, pause, holdUntil=hold_until()).status_code == 404
    with engine.begin() as c:
        c.execute(db.project_members.update().where(db.project_members.c.id == membership).values(revoked_at=None))

    # The same authenticated person is blocked when the server is not bound to the synthetic policy schema.
    with serve(engine.url, tmp_path, extra_env={'PMO_SYNTHETIC_POLICY_SCHEMA': ''}) as restricted:
        restricted.cookies.update(delegate.cookies)
        restricted.headers['X-CSRF-Token'] = delegate.headers['X-CSRF-Token']
        assert not item(restricted, pause)['canDecide']
        denied = judgment(restricted, pause, holdUntil=hold_until())
        assert (denied.status_code, denied.json()['code']) == (403, 'POLICY_PENDING')

    uid = delegate.get('auth/me').json()['accountId']
    with engine.begin() as c:
        c.execute(db.user_roles.insert().values(user_id=uid, role='delegator'))
    token = issue(delegate, pid, ('pmo_project_get',))
    denied = api.post(f"/api/v1/approvals/{pause['requestId']}/decide", headers={**headers(pause['revision']),
        'Authorization': f'Bearer {token}'}, json={'decision': 'approve', 'rationale': 'Attempted Agent judgment.',
        'holdUntil': hold_until()})
    assert (denied.status_code, denied.json()['code']) == (403, 'FORBIDDEN')
    expiry = hold_until()
    response = judgment(delegate, pause, holdUntil=expiry)
    receipt = data(response)
    ref = f'transition-grant:{gid}'
    assert receipt['authorityRef'] == ref and receipt['authoritySourceRef'] == 'SYNTHETIC-AUTH-12'
    assert receipt == item(pm, pause) and stage(pm, project)['stage'] == 'on_hold'
    assert ref in response.json()['meta']['sourceRefs']
    with engine.connect() as c:
        payload, digest = c.execute(sa.text('SELECT payload,payload_hash FROM project_transition_outcomes WHERE approval_id=:id'),
            {'id': UUID(pause['requestId'])}).one()
        assert fingerprint(payload) == digest
        authority = payload['authorityGrant']
        assert (authority['id'], authority['person_id'], authority['membership_id'], authority['project_id']) == (
            str(gid), str(people['outsider']), str(membership), pid)
        row = c.execute(sa.text('SELECT decision_id,source_refs FROM audit_logs WHERE request_id=:id'),
            {'id': response.headers['X-Request-ID']}).one()
        assert str(row.decision_id) == receipt['decisionId'] and ref in row.source_refs
        assert effects(c, pause) == (1, 1, 1, 1, 1, 1)
    resume = data(propose(pm, project, 'executing'))
    assert data(judgment(delegate, resume))['status'] == 'applied'
    assert stage(pm, project)['stage'] == 'executing'


def test_delegate_can_decide_sponsors_intent_but_not_agents_own_delegated_proposal(live, team):
    engine, api, _, _ = live
    pid, people, users, _ = team
    pm, sponsor, reviewer = (users[key] for key in ('pm', 'sponsor', 'reviewer'))
    project, _, _ = begin_execution(live, team)
    with engine.begin() as c:
        for key in ('pm', 'reviewer'):
            grant(c, pid, people[key], ('executing', 'terminated'))
    sponsor_intent = data(propose(sponsor, project, 'terminated'))
    token = issue(pm, pid, ('pmo_transition_request',))
    agent_intent = data(api.post(f'/api/v1/{project}/transition-requests', headers={**headers(sponsor_intent['revision']),
        'Authorization': f'Bearer {token}'}, json={'toStage': 'terminated', 'reason': 'Proposed by PM delegated token.'}))
    assert judgment(pm, agent_intent).json()['code'] == 'INDEPENDENT_APPROVER_REQUIRED'
    assert judgment(sponsor, sponsor_intent).json()['code'] == 'INDEPENDENT_APPROVER_REQUIRED'
    assert data(judgment(reviewer, sponsor_intent))['status'] == 'applied'
    assert stage(pm, project)['stage'] == 'terminated' and item(reviewer, agent_intent)['stale']


def test_formal_grant_cannot_replace_initiator_confirmation_or_atomic_decision(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    pm, reviewer, initiator = (users[key] for key in ('pm', 'reviewer', 'initiator'))
    project, review = start_feasibility(live, team)
    with engine.begin() as c:
        grant(c, pid, people['reviewer'], ('feasibility_review', 'terminated'))
    req = data(propose(pm, project, 'terminated'))
    assert judgment(reviewer, req).json()['code'] == 'TERMINATION_INITIATOR_CONFIRMATION_REQUIRED'
    signoff = data(initiator.post(f"approvals/{req['requestId']}/confirm-termination", headers=headers(req['revision']),
        json={'reason': 'Originator confirms this synthetic termination.'}))
    assert signoff['initiatorConfirmedAt']
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER test_formal_authority_rollback BEFORE INSERT ON outbox_events '
                          'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    key = headers(req['revision'])
    try:
        assert judgment(reviewer, req, key=key).status_code == 500
        assert bench(reviewer, review)['review']['status'] == 'pending'
        assert stage(pm, project)['stage'] == 'feasibility_review'
        with engine.connect() as c:
            assert effects(c, req) == (0, 0, 0, 0, 0, 0)
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER test_formal_authority_rollback ON outbox_events')
    keys = [key, headers(req['revision']), key]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda h: judgment(reviewer, req, key=h), keys))
    # Either key may win; all responses with the winning key agree and no second decision is written.
    assert [r.status_code for r in results] in ([200, 409, 200], [409, 200, 409])
    successes = [r.json() for r in results if r.is_success]
    assert successes and all(response == successes[0] for response in successes)
    assert all(r.json()['code'] == 'VERSION_CONFLICT' for r in results if not r.is_success)
    winning_key = next(h for h, r in zip(keys, results, strict=True) if r.is_success)
    assert judgment(reviewer, req, key=winning_key).json() == successes[0]
    assert judgment(reviewer, req, key=winning_key, decision='reject').json()['code'] == 'IDEMPOTENCY_CONFLICT'
    assert stage(pm, project)['stage'] == 'terminated'
    stopped = bench(reviewer, review)
    assert stopped['review']['verdict'] == 'terminated' and stopped['decision']['decisionId'] == successes[0]['data']['decisionId']
    with engine.connect() as c:
        assert effects(c, req) == (1, 1, 1, 1, 1, 1)


def test_revocation_wins_if_already_writing_when_decision_waits(live, team):
    engine, _, _, _ = live
    pid, people, users, _ = team
    pm, reviewer = (users[key] for key in ('pm', 'reviewer'))
    project, _, _ = begin_execution(live, team)
    req = data(propose(pm, project, 'on_hold'))
    with engine.begin() as c:
        gid = grant(c, pid, people['reviewer'], ('executing', 'on_hold'))
    assert item(reviewer, req)['canDecide']
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.begin() as revoke_tx, engine.connect().execution_options(isolation_level='AUTOCOMMIT') as watch:
            writer = revoke_tx.scalar(sa.text('SELECT pg_backend_pid()'))
            revoke(revoke_tx, gid)
            approval = pool.submit(judgment, reviewer, req, holdUntil=hold_until())
            deadline = monotonic() + 3
            blocked = False
            while not approval.done() and monotonic() < deadline:
                blocked = watch.scalar(sa.text('''SELECT EXISTS (SELECT 1 FROM pg_stat_activity s
                    WHERE :pid=ANY(pg_blocking_pids(s.pid)))'''), {'pid': writer})
                if blocked:
                    break
                sleep(.02)
        assert blocked, 'the real HTTP decision never waited on the grant row lock'
        denied = approval.result(timeout=10)
    assert (denied.status_code, denied.json()['code']) == (403, 'INDEPENDENT_APPROVER_REQUIRED')
    assert not item(reviewer, req)['canDecide'] and stage(pm, project)['stage'] == 'executing'
    with engine.connect() as c:
        assert effects(c, req) == (0, 0, 0, 0, 0, 0)


def test_grant_storage_enforces_tenant_edge_validity_and_preserves_history(live, team):
    pid, people, _, _ = team
    with live[0].begin() as c:
        outside_org = c.scalar(db.organizations.insert().values(name='Synthetic separate org', status='active').returning(db.organizations.c.id))
        outside_person = uuid4()
        c.execute(db.persons.insert().values(id=outside_person, name='Foreign', created_by=outside_person, org_id=outside_org, is_active=True))
        hold = ('executing', 'on_hold')
        for person, edge, expected in [(outside_person, hold, 'fk_transition_grants_person_org'),
                (people['reviewer'], ('retrospective', 'archived'), 'ck_transition_grants_edge')]:
            with pytest.raises(sa.exc.IntegrityError, match=expected), c.begin_nested():
                grant(c, pid, person, edge)
        stamp = datetime.now(UTC)
        with pytest.raises(sa.exc.IntegrityError, match='ck_transition_grants_validity'), c.begin_nested():
            grant(c, pid, people['reviewer'], hold, starts=stamp, expires=stamp)
        gid = grant(c, pid, people['reviewer'], hold)
        with pytest.raises(sa.exc.IntegrityError, match='only one revocation'), c.begin_nested():
            c.execute(sa.text("UPDATE project_transition_grants SET source_ref='ALTERED' WHERE id=:id"), {'id': gid})
        revoke(c, gid)
        with pytest.raises(sa.exc.IntegrityError, match='only one revocation'), c.begin_nested():
            c.execute(sa.text('UPDATE project_transition_grants SET revoked_at=NULL,revocation_ref=NULL WHERE id=:id'), {'id': gid})
        with pytest.raises(sa.exc.DBAPIError, match='append-only audit'), c.begin_nested():
            c.execute(sa.text('DELETE FROM project_transition_grants WHERE id=:id'), {'id': gid})
