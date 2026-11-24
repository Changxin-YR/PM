"""Q3: current forecast, approved baseline and LR-03 remain independently traceable."""
from datetime import timedelta

import sqlalchemy as sa

from test_health import code, prepare, sample
from test_reference_vectors_permissions import mcp_calls
from test_reference_vectors_signals import events
from test_reviews import data, headers, issue, team as team


def test_forecast_slip_remains_until_independent_plan_change_applies(live, team):
    pid, people, users, _ = team
    pm, reviewer, outsider = (users[who] for who in ('pm', 'reviewer', 'outsider'))
    project, first_day = prepare(live, team)
    with live[0].begin() as c:
        c.execute(sa.text('''INSERT INTO change_approval_grants
            VALUES (:p,:reviewer,'milestone','synthetic Q3',now()+interval '1 hour',NULL)'''),
            {'p': pid, 'reviewer': people['reviewer']})
    target = next(row for row in data(pm.get(project + '/milestones'))['items'] if row['plannedDate'] == first_day.isoformat())
    forecast = (first_day + timedelta(days=4)).isoformat()
    reason = 'The independent supplier forecast moved four days.'
    changed = data(pm.patch(f"milestones/{target['milestoneId']}/forecast", headers=headers(target['revision']),
        json={'newDate': forecast, 'reason': reason}))
    assert (changed['plannedDate'], changed['forecastDate']) == (target['plannedDate'], forecast)
    assert not data(pm.get(project + '/changes'))['items']
    opened, = events(pm, project, 'LR-03')
    assert (opened['status'], opened['plannedDate'], opened['forecastDate'], opened['reason']) == (
        'open', target['plannedDate'], forecast, reason)

    proposal = data(pm.post(project + '/changes', headers=headers(changed['revision']), json={
        'targetType': 'milestone', 'targetId': target['milestoneId'], 'before': {'plannedDate': target['plannedDate']},
        'after': {'plannedDate': forecast}, 'reason': 'Request a formal change to the committed milestone date.'}))
    assert proposal['status'] == 'pending'
    assert events(pm, project, 'LR-03') == [opened]
    before = sample(pm, project)
    assert before['timeLine'] == 'yellow' and opened['alertId'] in before['eventIds']

    verdict = data(reviewer.post(f"changes/{proposal['changeId']}/approve", headers=headers(proposal['revision']), json={
        'rationale': 'Checked the previous baseline and the revised customer schedule independently.'}))
    record, = data(pm.get(project + '/changes'))['items']
    assert record == verdict and record['status'] == 'applied'
    assert record['before'] == {'plannedDate': target['plannedDate']} and record['after'] == {'plannedDate': forecast}
    assert record['decidedBy'] == str(people['reviewer'])
    closed, = events(pm, project, 'LR-03')
    assert (closed['alertId'], closed['status'], closed['plannedDate']) == (opened['alertId'], 'resolved', target['plannedDate'])
    after = sample(pm, project)
    assert after['timeLine'] == 'green' and opened['alertId'] not in after['eventIds']
    time_travel = data(pm.get(project + '/health', params={'asOf': before['asOf']}))
    assert time_travel['historical'] and time_travel['snapshotId'] == before['snapshotId']
    assert time_travel['timeLine'] == 'yellow' and opened['alertId'] in time_travel['eventIds']
    code(outsider.get(project + '/alerts'), 404, 'RESOURCE_NOT_FOUND')

    token = issue(pm, pid, ['pmo_alert_list', 'pmo_milestone_list'])
    alerts, milestones = mcp_calls(live, token, [('pmo_alert_list', {'projectId': pid}), ('pmo_milestone_list', {'projectId': pid})])
    assert not alerts.isError and not milestones.isError
    alert, = [a for a in alerts.structuredContent['data']['items'] if a['ruleId'] == 'LR-03']
    assert (alert['alertId'], alert['status']) == (closed['alertId'], 'resolved')
    persisted = next(m for m in milestones.structuredContent['data']['items'] if m['milestoneId'] == target['milestoneId'])
    assert persisted['plannedDate'] == persisted['forecastDate'] == forecast

    ordinary = next(row for row in data(pm.get(project + '/milestones'))['items'] if row['milestoneId'] != target['milestoneId'])
    with live[0].begin() as c:
        c.execute(sa.text('UPDATE milestones SET is_key=false WHERE id=:id'), {'id': ordinary['milestoneId']})
    green = sample(pm, project)
    slipped = (first_day + timedelta(days=12)).isoformat()
    data(pm.patch(f"milestones/{ordinary['milestoneId']}/forecast", headers=headers(ordinary['revision']),
        json={'newDate': slipped, 'reason': 'Local documentation is forecast twelve days late.'}))
    non_key, = [row for row in events(pm, project, 'LR-03') if row['status'] == 'open']
    assert non_key['forecastDate'] == slipped
    assert any(ref.startswith(f"milestone:{ordinary['milestoneId']}@") for ref in non_key['sourceRefs'])
    unchanged = sample(pm, project)
    assert non_key['alertId'] in unchanged['eventIds']
    assert (unchanged['timeLine'], unchanged['health']) == (green['timeLine'], green['health'])
