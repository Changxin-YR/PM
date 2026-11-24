from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.outbox import dispatch
from pmo.rule_scan import scan_locked
from test_reference_vectors_governance import feasibility
from test_reference_vectors_permissions import mcp_calls
from test_reference_vectors_signals import events, queued
from test_reviews import data, decide, headers, issue, score, team as team


def test_only_unfinished_feasibility_conditions_warn_after_due_time(live, team):
    engine = live[0]
    pid, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    project, review = feasibility(live, team)
    score(reviewer, review, [3] * 5)
    due = datetime.now(UTC) + timedelta(hours=1)
    closed = data(decide(reviewer, review, verdict='conditional_pass', conditions=[
        {'title': 'Collect recovered test data', 'assigneeId': str(people['reviewer']), 'dueAt': due.isoformat()},
        {'title': 'Review the second source', 'assigneeId': str(people['pm']), 'dueAt': (due + timedelta(days=2)).isoformat()},
    ]))
    assert closed['decision']['stage'] == 'executing'
    task = next(t for t in closed['tasks'] if t['title'] == 'Collect recovered test data')
    record = data(pm.get(project + '/tasks'))
    data(pm.post(project + '/tasks', headers=headers(record['projectRevision']), json={
        'title': 'Manual review follow-up (not a condition)', 'sourceType': 'review', 'sourceId': review.split('/')[-1],
        'assigneeId': str(people['pm']), 'dueAt': due.isoformat()}))

    def scan(at):
        with engine.begin() as c:
            p = c.execute(sa.select(db.projects).where(db.projects.c.id == pid).with_for_update()).mappings().one()
            return scan_locked(c, p, at)

    at_deadline = scan(due)
    assert not events(pm, project, 'LR-02')
    first = scan(due + timedelta(seconds=1))
    assert scan(due + timedelta(seconds=2)).id == first.id
    alerts = events(pm, project, 'LR-02')
    alert, = alerts
    assert alert['severity'] == 'yellow' and alert['status'] == 'open'
    assert datetime.fromisoformat(alert['dueAt']) == due and alert['taskTitle'] == task['title']
    assert alert['alertId'] in first.rule_event_ids and first.results == at_deadline.results
    assert any(ref.startswith(f"task:{task['taskId']}@") for ref in alert['sourceRefs'])
    assert [r['recipientId'] for r in queued(engine, alerts)] == [str(people['pm'])]
    token = issue(pm, pid, ['pmo_alert_list'])
    response, = mcp_calls(live, token, [('pmo_alert_list', {'projectId': pid})])
    assert not response.isError
    agent, = [row for row in response.structuredContent['data']['items'] if row['ruleId'] == 'LR-02']
    assert agent == {key: alert[key] for key in ('alertId', 'projectId', 'ruleId', 'severity', 'status', 'asOf', 'sourceRefs')}

    dispatch(engine, 500)
    dispatch(engine, 500)
    overview = data(pm.get('analytics/health-alerts', params={'ruleId': 'LR-02'}))['items']
    delivered, = overview
    assert delivered['alertId'] == alert['alertId'] and delivered['notifiedAt']
    n = db.notifications
    with engine.connect() as c:
        assert c.execute(sa.select(n.c.recipient_person_id, n.c.delivery_status)
            .where(n.c.rule_event_id == alert['alertId'])).one() == (people['pm'], 'delivered')

    def update(fields):
        item = next(row for row in data(reviewer.get(project + '/tasks'))['items'] if row['taskId'] == task['taskId'])
        data(reviewer.patch(f"tasks/{item['taskId']}", headers=headers(item['revision']), json={'fieldsTask': fields}))

    update({'status': 'cancelled'})
    with engine.connect() as c:
        assert c.scalar(sa.text('SELECT dirty FROM project_rule_scan_state WHERE project_id=:p'), {'p': pid})
    scan(due + timedelta(seconds=3))
    assert events(pm, project, 'LR-02')[0]['status'] == 'open'
    postponed = due + timedelta(minutes=30)
    update({'dueAt': postponed.isoformat(), 'status': 'in_progress'})
    scan(due + timedelta(seconds=4))
    resolved, = events(pm, project, 'LR-02')
    assert (resolved['alertId'], resolved['status']) == (alert['alertId'], 'resolved')
    assert len(queued(engine, alerts)) == 1

    scan(postponed + timedelta(seconds=1))
    current, = [row for row in events(pm, project, 'LR-02') if row['status'] == 'open']
    assert current['alertId'] != alert['alertId'] and datetime.fromisoformat(current['dueAt']) == postponed
    update({'status': 'done'})
    scan(postponed + timedelta(seconds=2))
    assert {row['status'] for row in events(pm, project, 'LR-02')} == {'resolved'}

    # This read-side warning follows the live condition even before the next rule scan.
    dashboard = f'project-overview/{pid}'
    past_due = datetime.now(UTC) - timedelta(minutes=1)
    update({'status': 'in_progress', 'dueAt': past_due.isoformat()})
    response = pm.get(dashboard)
    summary = data(response)
    dashboard_warning, = summary['overdueConditions']
    assert summary['overdueConditionCount'] == 1
    assert (dashboard_warning['taskId'], dashboard_warning['title']) == (task['taskId'], task['title'])
    assert datetime.fromisoformat(dashboard_warning['dueAt']) == past_due
    assert any(ref.startswith(f"task:{task['taskId']}@") for ref in response.json()['meta']['sourceRefs'])
    assert 'overdueConditions' not in data(users['initiator'].get(dashboard))
    update({'status': 'done'})
    cleared = data(pm.get(dashboard))
    assert 'overdueConditions' not in cleared
    assert {key: val for key, val in summary['indicators'].items() if key != 'asOf'} == {
        key: val for key, val in cleared['indicators'].items() if key != 'asOf'}
