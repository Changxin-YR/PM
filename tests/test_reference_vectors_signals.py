"""Run the frozen health and warning inputs against the authoritative calculation and scanner."""
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.health_calculation import TZ, WEEK, _timing, monday, week_key
from pmo.rule_scan import scan_locked
from test_health import close_for_lr08, prepare, sample, scan_lr08
from test_lifecycle import ANSWERS, begin_execution, execute, propose, stage
from test_reference_vectors import cases
from test_reference_vectors_governance import lr
from test_reports import body, sections
from test_reviews import data, headers, team as team


def events(client, project, rule):
    return [alert for alert in data(client.get(project + '/alerts'))['items'] if alert['ruleId'] == rule]


def queued(engine, alerts):
    e = db.outbox_events
    with engine.connect() as c:
        return c.scalars(sa.select(e.c.payload).where(e.c.event_type == 'rule.notify',
            e.c.payload['eventId'].astext.in_([a['alertId'] for a in alerts]))).all()


@pytest.mark.parametrize('case', cases('health', lambda c: 'plannedDate' in c))
def test_overdue_unfinished_key_milestone(case):
    assert case['stage'] == 'executing' and case['actualDate'] is None
    row = SimpleNamespace(id='fixed', revision=1, name='Fixed milestone', is_cancelled=False, is_key=case['isKey'],
        planned_date=date.fromisoformat(case['plannedDate']), forecast_date=date.fromisoformat(case['forecastDate']), actual_date=None)
    timing, details, _, refs, gaps = _timing([row], date.fromisoformat(case['today']), None)
    assert timing == case['expectedTime'] and details[0]['color'] == timing
    assert not gaps and refs == ['milestone:fixed@1']


@pytest.mark.parametrize('case', lr('LR-03'))
def test_delay_warning(case, live, team):
    _, people, users, _ = team
    pm = users['pm']
    assert not case['hasApprovedPlanChange']
    project, _, _ = begin_execution(live, team, first_day=date.fromisoformat(case['plannedDate']))
    milestone = next(m for m in data(pm.get(project + '/milestones'))['items'] if m['plannedDate'] == case['plannedDate'])
    assert milestone['plannedDate'] == case['plannedDate']
    path = f"milestones/{milestone['milestoneId']}/forecast"
    attempt = headers(milestone['revision'])
    command = {'newDate': case['forecastDate'], 'reason': 'Updated estimate from the synthetic schedule.'}
    saved = pm.patch(path, headers=attempt, json=command)
    assert data(saved)['plannedDate'] == case['plannedDate']
    assert pm.patch(path, headers=attempt, json=command).json() == saved.json()
    sample(pm, project)
    rows = events(pm, project, 'LR-03')
    assert len(rows) == int(case['expectedTriggered'])
    if rows:
        assert (rows[0]['status'], rows[0]['severity']) == ('open', 'yellow')
        assert rows[0]['plannedDate'] == case['plannedDate'] and rows[0]['forecastDate'] == case['forecastDate']
        assert [r['recipientId'] for r in queued(live[0], rows)] == [str(people['pm'])]


@pytest.mark.parametrize('case', lr('LR-04'))
def test_cost_warning(case, live, team):
    _, people, users, _ = team
    pm = users['pm']
    project, _ = prepare(live, team)
    for kind, budget in [('money', Decimal(100)), ('effort', Decimal(10))]:
        forecast = budget * (1 + Decimal(case[kind + 'Variance']))
        rev = data(pm.get(project + '/costs'))['plan']['revision']
        data(pm.put(project + '/cost-forecasts', headers=headers(rev), json={
            'type': kind, 'forecastRemaining': f'{forecast:.2f}', 'reason': 'Fixed reference forecast.'}))
    first = sample(pm, project)
    assert sample(pm, project)['snapshotId'] == first['snapshotId']
    rows = events(pm, project, 'LR-04')
    assert len(rows) == int(case['expectedTriggered'])
    assert first['costLine'] == ('red' if case['expectedTriggered'] else 'yellow')
    assert [r['recipientId'] for r in queued(live[0], rows)] == [str(people['sponsor'])] * len(rows)
    assert not [r for r in data(pm.get(project + '/tasks'))['items'] if r['title'].startswith('LR-04')]


@pytest.mark.parametrize('case', cases('lrRules', lambda c: 'consecutiveDueMissingWeeklyReports' in c and 'keyMilestoneProgressClaim' not in c))
def test_due_week_threshold(case, live, team):
    pid, people, users, _ = team
    pm = users['pm']
    project, _ = prepare(live, team)
    assert stage(pm, project)['stage'] == case['stage']
    previous = sample(pm, project)
    with live[0].begin() as c:
        began = c.scalar(sa.text("SELECT transitioned_at FROM project_stage_transitions WHERE project_id=:p AND to_stage='executing'"), {'p': pid})
        start = monday(began.astimezone(TZ).date())
        due = datetime.combine(start + case['consecutiveDueMissingWeeklyReports'] * WEEK, time(18), TZ)
        p = c.execute(sa.select(db.projects).where(db.projects.c.id == pid).with_for_update()).mappings().one()
        first = scan_locked(c, p, due)
        assert scan_locked(c, p, due).id == first.id
        weekly = first.input_revisions['weekly']
        assert len(weekly['missingPeriods']) == case['consecutiveDueMissingWeeklyReports']
        assert weekly['twoMissed'] is case['expectedTriggered']
        assert first.results['publication'] == ('withheld' if case['expectedTriggered'] else 'preview')
        assert ('timeLine' not in first.results) is case['expectedTriggered']
    rows = events(pm, project, 'LR-06')
    assert len(rows) == int(case['expectedTriggered'])
    assert [r['recipientId'] for r in queued(live[0], rows)] == [str(people['pm'])] * len(rows)
    assert data(pm.get(project + '/health', params={'asOf': previous['asOf']}))['snapshotId'] == previous['snapshotId']


@pytest.mark.parametrize('case', [*cases('health', lambda c: 'progressClaimed' in c),
                                 *cases('lrRules', lambda c: 'keyMilestoneProgressClaim' in c)])
def test_contemporaneous_progress_requires_evidence(case, live, team):
    _, people, users, _ = team
    pm = users['pm']
    project, _ = prepare(live, team)
    assert stage(pm, project)['stage'] == case['stage']
    claimed = case.get('progressClaimed', case.get('keyMilestoneProgressClaim'))
    expected = case.get('expectedDataTrust', 'untrusted' if case.get('expectedTriggered') else 'trusted')
    milestone = data(pm.get(project + '/milestones'))['items'][0]
    assert milestone['isKey'] is case.get('isKey', True)
    claims = [{'milestoneId': milestone['milestoneId'], 'claim': 'progress', 'evidenceRefs': case.get('evidenceRefs', [])}] if claimed else []
    data(pm.post(project + '/reports', headers=headers(), json=body(period=week_key(datetime.now(TZ).date()),
        structuredFields=sections(milestone_progress=claims))))
    receipt = sample(pm, project)
    assert receipt['dataTrust'] == expected
    rows = events(pm, project, 'LR-06')
    assert len(rows) == int(expected == 'untrusted')
    assert [r['recipientId'] for r in queued(live[0], rows)] == [str(people['pm'])] * len(rows)


@pytest.mark.parametrize('case', lr('LR-08'))
def test_retrospective_reminder(case, live, team):
    pid, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    if case['state'] == 'closed':
        project, cutoff = close_for_lr08(live, team)
        if case['retrospectiveDone']:
            data(execute(pm, project, data(propose(pm, project, 'retrospective'))))
            retro = project + '/retrospective'
            data(pm.put(retro, headers=headers(data(pm.get(retro))['revision']), json={'answers': ANSWERS}))
            for i in range(case['confirmedLessonCount']):
                row = data(pm.post(project + '/lessons', headers=headers(), json={'category': 'delivery',
                    'what': f'Collect the original evidence for item {i}.', 'rootCause': 'No original retained.',
                    'advice': 'Keep the reconciliation worksheet.'}))
                data(reviewer.post(f"lessons/{row['lessonId']}/confirm", headers=headers(row['revision']), json={}))
        at = cutoff + timedelta(days=case['daysSinceClosed'] - 14)
    else:
        project, _ = prepare(live, team)
        assert stage(pm, project)['stage'] == case['state']
        at = datetime.now(UTC) + timedelta(days=case['daysSinceClosed'])
    for _ in range(2):
        scan_lr08(live[0], pid, at)
    rows = events(pm, project, 'LR-08')
    assert len(rows) == int(case['expectedTriggered'])
    assert [r['recipientId'] for r in queued(live[0], rows)] == [str(people['sponsor'])] * len(rows)
