"""Q7 evidence is tied to the saved observation, not today's mutable inputs."""
from copy import deepcopy
from datetime import date, datetime, timedelta

import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.contracts import RULE_VERSION
from pmo.health_calculation import TZ, observe, week_key
from test_health import code, prepare, sample
from test_reference_vectors_permissions import bearer
from test_reports import body, sections
from test_reviews import data, headers, issue, team as team


PATH = 'analytics/weekly-health'


def yellow_sample(live, team):
    _, _, users, _ = team
    pm = users['pm']
    project, day = prepare(live, team)
    target = next(m for m in data(pm.get(project + '/milestones'))['items'] if m['plannedDate'] == day.isoformat())
    data(pm.patch(f"milestones/{target['milestoneId']}/forecast", headers=headers(target['revision']), json={
        'newDate': (day + timedelta(days=4)).isoformat(), 'reason': 'Synthetic schedule now projects four days of delay.'}))
    return project, target, sample(pm, project)


def test_weekly_health_keeps_current_receipt_separate_from_earlier_signals(live, team):
    pid, _, users, _ = team
    pm, outsider = users['pm'], users['outsider']
    project, milestone, yellow = yellow_sample(live, team)
    first = data(pm.get(PATH))
    assert (first['sampledProjects'], first['total']) == (1, 1)
    row, = first['items']
    initial = row['latest']
    assert row['projectId'] == pid and initial['snapshotId'] == yellow['snapshotId']
    assert (initial['health'], initial['timeLine'], initial['costLine'], initial['qualityLine']) == ('green', 'yellow', 'green', 'green')
    cause, = initial['causes']
    assert cause == {'line': 'time', 'color': 'yellow', 'code': 'MILESTONE_DRIFT', 'name': milestone['name'],
        'plannedDate': milestone['plannedDate'], 'forecastDate': (date.fromisoformat(milestone['plannedDate']) + timedelta(days=4)).isoformat(),
        'delayDays': 4, 'sourceRefs': [f"milestone:{milestone['milestoneId']}@{milestone['revision']+1}"]}
    rule, = initial['ruleEvents']
    assert rule['ruleId'] == 'LR-03' and rule['alertId'] in yellow['eventIds']

    rev = data(pm.get(project + '/costs'))['plan']['revision']
    data(pm.put(project + '/cost-forecasts', headers=headers(rev), json={
        'type': 'money', 'forecastRemaining': '116.25', 'reason': 'Fixed synthetic outstanding commitments.'}))
    red = sample(pm, project)
    assert red['health'] == 'red'
    next_row, = data(pm.get(PATH))['items']
    assert next_row['latest']['snapshotId'] == red['snapshotId']
    assert 'earlierSignal' not in next_row
    cost, = [line for line in next_row['latest']['causes'] if line['line'] == 'money']
    assert cost['varianceRate'] == '0.1625' and cost['color'] == 'red'

    period = week_key(datetime.fromisoformat(red['asOf']).astimezone(TZ).date())
    data(pm.post(project + '/reports', headers=headers(), json=body(period=period, structuredFields=sections(milestone_progress=[{
        'milestoneId': milestone['milestoneId'], 'claim': 'progress', 'evidenceRefs': []}]))))
    missing = sample(pm, project)
    page = data(pm.get(PATH, params={'pageSize': 1}))
    now, = page['items']
    newest = now['latest']
    assert newest['snapshotId'] == missing['snapshotId'] and newest['health'] == 'untrusted' and newest['publication'] == 'withheld'
    assert not newest['causes'] and not {'timeLine', 'costLine', 'qualityLine'} & newest.keys()
    assert 'LR06_CLAIM_WITHOUT_EVIDENCE' in newest['gaps'] and 'LR-06' in {x['ruleId'] for x in newest['ruleEvents']}
    assert now['earlierSignal']['snapshotId'] == red['snapshotId'] and now['earlierSignal']['costLine'] == 'red'
    same_week = data(pm.get(PATH, params={'weekStart': first['weekStart'], 'asOf': first['asOf']}))
    assert same_week['items'] == first['items']
    receipt = data(pm.get(project + '/health', params={'asOf': now['earlierSignal']['asOf']}))
    assert receipt['historical'] and receipt['snapshotId'] == red['snapshotId'] and receipt['inputHash'] == red['inputHash']
    empty = data(pm.get(PATH, params={'weekStart': page['weekStart'], 'asOf': page['asOf'], 'page': 2, 'pageSize': 1}))
    assert empty['total'] == 1 and not empty['items']

    start = date.fromisoformat(first['weekStart'])
    previous = data(pm.get(PATH, params={'weekStart': (start - timedelta(days=7)).isoformat()}))
    assert previous['sampledProjects'] == 0 and not previous['items']
    code(pm.get(PATH, params={'weekStart': (start - timedelta(days=1)).isoformat()}), 422, 'INVALID_HEALTH_WEEK')
    assert data(outsider.get(PATH))['sampledProjects'] == 0
    token = issue(pm, pid, ['pmo_health_get'])
    code(live[1].get('/api/v1/' + PATH, headers=bearer(token)), 403, 'FORBIDDEN')


def test_weekly_health_reads_pre_dispute_p0_receipt_without_reclassifying(live, team):
    pid, _, users, _ = team
    pm = users['pm']
    project, _ = prepare(live, team, priority='P0')
    with live[0].begin() as c:
        at = c.scalar(sa.select(sa.func.clock_timestamp()))
        p = c.execute(sa.select(db.projects).where(db.projects.c.id == pid).with_for_update()).mappings().one()
        obs = observe(c, p, at)
        (requirement,) = obs.inputs['quality']['schedule']
        assert obs.results['dataTrust'] == 'trusted' and requirement['color'] == 'red' and not requirement['disputed']
        # This flag was introduced only with customer disputes. Preserve the old receipt format.
        del requirement['disputed']
        public_trace = deepcopy(obs.inputs)
        assert public_trace['time'][0]['completion'] is None
        # Extra storage diagnostics must not become part of the public DTO or its calculated values.
        obs.inputs['internalDiagnostic'] = 'synthetic private trace'
        requirement['internalReviewerNote'] = 'synthetic private note'
        snapshots = db.health_snapshots
        saved = c.execute(snapshots.insert().values(project_id=pid, as_of=at, input_hash=obs.digest,
            data_trust='trusted', rule_version=RULE_VERSION, input_revisions=obs.inputs, results=obs.results,
            source_refs=obs.refs, rule_event_ids=[]).returning(snapshots.c.id, snapshots.c.input_hash)).one()

    row, = data(pm.get(PATH, params={'asOf': at.isoformat()}))['items']
    actual = row['latest']
    assert (actual['snapshotId'], actual['health'], actual['qualityLine']) == (str(saved.id), 'red', 'red')
    cause, = actual['causes']
    assert cause['code'] == 'P0_UNCONFIRMED'
    assert cause['sourceRefs'] == [f"requirement:{requirement['requirement_id']}@{requirement['revision']}"]
    old = data(pm.get(project + '/health', params={'asOf': at.isoformat()}))
    assert old['historical'] and old['inputHash'] == saved.input_hash
    assert old['inputs'] == public_trace
    with live[0].connect() as c:
        persisted = c.execute(sa.select(snapshots.c.input_revisions, snapshots.c.input_hash)
                              .where(snapshots.c.id == saved.id)).one()
        assert (persisted.input_revisions, persisted.input_hash) == (obs.inputs, obs.digest)
