from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import sqlalchemy as sa

from pmo import schema_v1 as db
from test_facts import fact_path, medium, post, proposal, report_path
from test_reports import body, data, deny, headers, sections, team as team


def submitted(pm, path, *, amount='4.00', **fields):
    return data(pm.post(path, headers=headers(),
        json=body(structuredFields=sections(effort_person_days=amount), **fields)))


def publish(pm, report):
    return data(pm.post(report_path(report) + '/confirm', headers=headers(report['revision'])))


def test_portfolio_limits_current_publication_to_caller_report_scope(team, live, actor):
    pm, member, reviewer, outsider = (team.users[k] for k in ('pm', 'reporter', 'reviewer', 'outsider'))
    target, other = str(team.pid), str(team.other)
    url = 'analytics/weekly-reports?weekStart=2024-12-30'
    initial = publish(pm, submitted(member, team.path, narrative='Protected details'))
    provisional = submitted(pm, f'projects/{other}/reports', amount='8.00')
    latest = publish(pm, submitted(pm, team.path, amount='5.00'))
    assert initial['versionNo'] < latest['versionNo']
    pm_result = pm.get(url)
    rows = {row['projectId']: row for row in data(pm_result)['items']}
    assert set(rows) == {target, other}
    row = rows[target]
    assert row['status'] == 'published' and row['projectId'] == target
    assert row['reportId'] == latest['reportId'] and row['declaredPersonDays'] == '5.00'
    assert row['sourceRef'] == f"report:{latest['reportId']}@{latest['revision']}"
    assert pm_result.json()['meta']['sourceRefs'] == [row['sourceRef']]
    assert 'Protected details' not in pm_result.text
    assert rows[other]['status'] == 'no_readable_publication' and 'reportId' not in rows[other]
    assert provisional['reportId'] not in pm_result.text
    assert data(reviewer.get(f'{url}&projectId={target}'))['items'] == [row]
    deny(reviewer.get(f'{url}&projectId={other}'), 404, 'RESOURCE_NOT_FOUND')
    deny(reviewer.get(f'{url}&projectId={uuid4()}'), 404, 'RESOURCE_NOT_FOUND')
    assert data(outsider.get(url))['items'] == [rows[other]]
    member_rows = {item['projectId']: item for item in data(member.get(url))['items']}
    assert member_rows[target]['status'] == member_rows[other]['status'] == 'no_readable_publication'
    assert latest['reportId'] not in member.get(url).text
    old_query = {'weekStart': '2024-12-30', 'projectId': target, 'asOf': initial['confirmedAt']}
    for reader in (pm, member, reviewer):
        historical = data(reader.get('analytics/weekly-reports', params=old_query))
        old_row, = historical['items']
        assert historical['asOf'] == initial['confirmedAt']
        assert old_row['reportId'] == initial['reportId'] and old_row['declaredPersonDays'] == '4.00'
    deny(pm.get('analytics/weekly-reports?weekStart=2024-12-31'), 422, 'INVALID_REPORT_WEEK')
    token, _ = actor(extra_actions=['human_weekly_portfolio_report_list'])
    deny(live[1].get(f'/api/v1/{url}', headers={'Authorization': f'Bearer {token}'}), 403, 'FORBIDDEN')


def test_published_weekly_effort_uses_pending_residual_and_independent_scoped_verification(team):
    pm, member, verifier = (team.users[k] for k in ('pm', 'reporter', 'reviewer'))
    with team.engine.begin() as c:
        c.execute(db.user_roles.insert(), [{'user_id': team.people[name].account, 'role': 'finance_verifier'}
            for name in ('pm', 'reviewer')])
    costs = f'projects/{team.pid}/cost-postings'
    when = '2025-01-02T10:00:00Z'

    def manual(ref, amount):
        row = data(pm.post(costs, headers=headers(), json={'type': 'effort', 'sourceRef': ref,
            'amount': amount, 'period': '2025-01'}))
        data(verify(verifier, row))
        return row

    def verify(actor, row, instant=when):
        return actor.post(f"cost-postings/{row['postingId']}/verify", headers=headers(1),
            json={'reason': 'Checked independent payroll sheet', 'occurredAt': instant})

    manual('Weekly signed timesheets', '2.00')
    pending = submitted(pm, team.path)
    path = report_path(pending) + '/effort-posting'
    deny(pm.get(path), 422, 'WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION')
    report = publish(pm, pending)
    preview = data(pm.get(path))
    assert preview['declaredPersonDays'] == '4.00' and preview['verifiedPersonDays'] == '2.00'
    assert preview['candidatePersonDays'] == '2.00' and preview['canPropose']
    assert preview['allowedPeriods'] == ['2024-12', '2025-01']
    assert not data(verifier.get(path))['canPropose']
    deny(member.get(path), 404, 'RESOURCE_NOT_FOUND')

    def send(amount, *, month='2025-01', key=None):
        return pm.post(path, headers=key or headers(report['revision']),
            json={'period': month, 'expectedAmount': amount})

    deny(send('2.00', month='2025-02'), 422, 'WEEKLY_EFFORT_PERIOD_INVALID')
    deny(send('2.00', key=headers(report['revision'] - 1)), 409, 'VERSION_CONFLICT')
    lost_response_key = headers(report['revision'])
    first = data(send('2.00', key=lost_response_key))
    assert first['status'] == 'pending'
    assert data(send('2.00', key=lost_response_key)) == first
    assert data(send('2.00')) == first
    deny(verify(pm, first), 403, 'INDEPENDENT_VERIFIER_REQUIRED')
    deny(verify(verifier, first, '2025-01-07T00:00:00Z'), 422, 'WEEKLY_EFFORT_OCCURRENCE_OUTSIDE_WEEK')
    assert data(pm.get(f'projects/{team.pid}/costs'))['effort']['actual'] == '2.00'
    manual('Later imported timesheets', '1.00')
    deny(verify(verifier, first), 409, 'WEEKLY_EFFORT_BALANCE_CHANGED')
    deny(send('2.00'), 409, 'WEEKLY_EFFORT_BALANCE_CHANGED')
    with ThreadPoolExecutor(2) as pool:
        first_attempt, second_attempt = list(pool.map(lambda _: data(send('1.00')), range(2)))
    assert first_attempt == second_attempt
    assert first_attempt['postingId'] != first['postingId']
    assert data(verify(verifier, first_attempt))['status'] == 'verified'
    assert data(pm.get(f'projects/{team.pid}/costs'))['effort']['actual'] == '4.00'
    preview = data(pm.get(path))
    assert preview['candidatePersonDays'] == '0.00' and not preview['canPropose']
    deny(send('1.00'), 409, 'WEEKLY_EFFORT_ALREADY_VERIFIED')
    deny(pm.post(costs, headers=headers(), json={'type': 'effort', 'sourceRef': first['sourceRef'],
        'amount': '2.00', 'period': '2025-01'}), 422, 'WEEKLY_EFFORT_DEDICATED_ROUTE')
    replacement = publish(pm, submitted(pm, team.path, amount='4.00'))
    deny(pm.get(path), 422, 'WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION')
    deny(verify(verifier, first), 422, 'WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION')
    assert data(pm.get(report_path(replacement) + '/effort-posting'))['candidatePersonDays'] == '0.00'


def test_weekly_fact_change_requires_explicit_pm_ref_and_preserves_previous_fact(team):
    pm, reviewer, member, outsider = (team.users[k] for k in ('pm', 'reviewer', 'reporter', 'outsider'))
    prev_report = publish(pm, submitted(pm, team.path, amount='3.00', period='2024-W52'))
    prev_fact = data(post(pm, prev_report, proposal(prev_report, '3.00', asOf='2024-12-23T08:00:00+08:00')))
    prev = data(medium(reviewer, prev_fact))
    previous_ref = f"fact:{prev['factId']}@{prev['revision']}"
    current_report = publish(pm, submitted(pm, team.path))
    picked = data(post(pm, current_report, proposal(current_report, '4.00')))
    current = data(medium(reviewer, picked))
    outside = data(post(pm, current_report, proposal(current_report, '4.00',
        subject='Outside this week', asOf='2025-01-07T08:00:00+08:00')))
    data(medium(reviewer, outside))
    path = report_path(current_report) + '/facts/weekly-comparison'
    result = pm.get(path)
    result_rows = {row['fact']['factId']: row for row in data(result)['items']}
    chosen = result_rows[current['factId']]
    assert chosen['comparison']['status'] == 'changed'
    assert chosen['comparison']['previous']['factRef'] == previous_ref
    assert chosen['canRequestClarification']
    assert result_rows[outside['factId']]['comparison']['status'] == 'outside_reporting_week'
    assert previous_ref in result.json()['meta']['sourceRefs']
    assert all(not row['canRequestClarification'] for row in data(reviewer.get(path))['items'])
    deny(member.get(path), 404, 'RESOURCE_NOT_FOUND')
    deny(outsider.get(path), 404, 'RESOURCE_NOT_FOUND')
    with team.engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(db.fact_clarifications.join(
            db.facts, db.fact_clarifications.c.fact_id == db.facts.c.id)).where(db.facts.c.project_id == team.pid)) == 0

    action = fact_path(current) + '/weekly-clarification'
    json = {'previousFactRef': previous_ref, 'confirmed': True, 'reason': '排查两周累计工时口径'}
    deny(reviewer.post(action, headers=headers(current['revision']), json=json), 403, 'FORBIDDEN')
    deny(pm.post(action, headers=headers(current['revision']), json={**json, 'confirmed': False}),
        422, 'WEEKLY_FACT_CONFIRMATION_REQUIRED')
    deny(pm.post(action, headers=headers(current['revision']), json={**json, 'previousFactRef': f'fact:{uuid4()}@2'}),
        409, 'WEEKLY_FACT_COMPARISON_CHANGED')
    stable = headers(current['revision'])
    response = pm.post(action, headers=stable, json=json)
    changed = data(response)
    assert changed['validationStatus'] == 'disputed' and changed['trust'] == current['trust']
    assert previous_ref in response.json()['meta']['sourceRefs']
    assert pm.post(action, headers=stable, json=json).json() == response.json()
    deny(pm.post(action, headers=headers(current['revision']), json=json), 409, 'VERSION_CONFLICT')
    with team.engine.connect() as c:
        case = c.execute(sa.select(db.fact_clarifications).where(db.fact_clarifications.c.fact_id == current['factId'])).mappings().one()
        assert case.status == 'open' and json['reason'] in case.reason and previous_ref in case.reason
        task = c.execute(sa.select(db.tasks).where(db.tasks.c.source_id == case.id)).mappings().one()
        assert task.assignee_id == team.people['pm'].id and task.status == 'todo'
        assert c.scalar(sa.select(db.facts.c.validation_status).where(db.facts.c.id == prev['factId'])) == 'verified'
    resolved = data(pm.post(fact_path(current) + '/clarify', headers=headers(changed['revision']),
        json={'reason': '对照核验原件后已澄清本期口径', 'correctedSourceRef': current['sourceRef']}))
    assert resolved['validationStatus'] == 'verified'
    with team.engine.connect() as c:
        assert c.scalar(sa.select(db.tasks.c.status).where(db.tasks.c.id == task.id)) == 'done'
