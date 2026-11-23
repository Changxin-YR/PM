"""Follow real command receipts across changes to their authority or input sources."""
from datetime import timedelta

import sqlalchemy as sa

from pmo import schema_v1 as db
from test_health import prepare
from test_lifecycle import stage
from test_reports import deny
from test_reviews import bench, data, headers, member, outcome, result_counts, score, start_charter, team as team


def test_outbox_failure_rolls_back_decision_and_reviewer_loss_rejects_replay(live, team):
    engine = live[0]
    pid, people, users, _ = team
    reviewer = users['reviewer']
    path = start_charter(pid, users)
    ready = score(reviewer, path, [4, 3, 3, 3])
    payload, key = outcome(ready), headers(ready['review']['revision'])
    with engine.begin() as c:
        member(c, pid, people['reviewer'], people['initiator'], 'member')
        before = result_counts(c, pid)
        c.exec_driver_sql('CREATE TRIGGER closeout_outbox_fault BEFORE INSERT ON outbox_events '
                          'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        deny(reviewer.post(path + '/decisions', json=payload, headers=key), 500, 'DATABASE_FAILURE')
        with engine.connect() as c:
            assert result_counts(c, pid) == before
        assert bench(reviewer, path)['review']['revision'] == ready['review']['revision']
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER closeout_outbox_fault ON outbox_events')
    saved = reviewer.post(path + '/decisions', json=payload, headers=key)
    assert data(saved)['decision']['stage'] == 'designing'
    assert reviewer.post(path + '/decisions', json=payload, headers=key).json() == saved.json()
    with engine.begin() as c:
        committed = result_counts(c, pid)
        assert committed[:7] == ('designing', 1, 1, before[3]+1, 1, 1, 1)
        m = db.project_members
        c.execute(m.update().where(m.c.project_id == pid, m.c.person_id == people['reviewer'], m.c.role == 'reviewer')
                  .values(revoked_at=sa.func.now()))
    assert bench(reviewer, path)['decision']['decisionId'] == data(saved)['decision']['decisionId']
    blocked = reviewer.post(path + '/decisions', json=payload, headers=key)
    deny(blocked, 403, 'REVIEWER_ASSIGNMENT_REQUIRED')
    with engine.connect() as c:
        assert result_counts(c, pid) == committed
        assert c.execute(sa.select(db.audit_logs.c.result, db.audit_logs.c.error_code).where(
            db.audit_logs.c.request_id == blocked.headers['X-Request-ID'])).one() == ('denied', 'REVIEWER_ASSIGNMENT_REQUIRED')


def test_history_keeps_frozen_project_and_source_revisions(live, team):
    pid, _, users, _ = team
    pm = users['pm']
    p, due = prepare(live, team)
    health = p + '/health'
    current = stage(pm, p)['revision']
    deny(users['reviewer'].post(health + '/refresh', headers=headers(current)), 403, 'FORBIDDEN')
    before = data(pm.post(health + '/refresh', headers=headers(current)))
    milestone, = data(pm.get(p + '/milestones'))['items'][:1]
    data(pm.patch(f"milestones/{milestone['milestoneId']}/forecast", headers=headers(milestone['revision']),
                  json={'newDate': (due + timedelta(days=8)).isoformat(), 'reason': 'New independent source evidence'}))
    # A governance import changes the project version without rewriting prior health evidence.
    with live[0].begin() as c:
        t = db.projects
        c.execute(t.update().where(t.c.id == pid).values(revision=current + 1))
    now = data(pm.get(health))
    assert now['projectRevision'] == current + 1 and now['inputs'] != before['inputs']
    old = data(pm.get(health, params={'asOf': before['asOf']}))
    history = data(pm.get(health + '/history'))['items'][0]
    for value in (old, history):
        assert value['historical'] is True and not value['canRefresh']
        for field in ('projectRevision', 'snapshotId', 'inputHash', 'ruleVersion', 'inputs', 'sourceRefs'):
            assert value[field] == before[field]
    with live[0].connect() as c:
        rows = c.execute(sa.select(db.health_snapshots.c.id, db.health_snapshots.c.input_hash).where(
            db.health_snapshots.c.project_id == pid)).all()
        assert [(str(row.id), row.input_hash) for row in rows] == [(before['snapshotId'], before['inputHash'])]
