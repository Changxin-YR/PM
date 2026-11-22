from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from pmo import alerts, projects
from pmo.audit_store import audit
from pmo.contracts import RULE_VERSION
from pmo.health_schema import events
from pmo.identity import Browser
from test_reference_vectors_permissions import bearer, denied_audit, mcp_calls
from test_reviews import data, headers, issue, team as team


def event(c, project, rule, period, at):
    reference = f'project:{project}@1'
    eid = c.scalar(events.insert().values(project_id=project, rule_id=rule, rule_version=RULE_VERSION,
        business_period=period, status='open', fingerprint=uuid4().hex, source_refs=[reference],
        input_snapshot={'reason': 'Synthetic authorized read detail only'}, created_at=at, updated_at=at).returning(events.c.id))
    return str(eid), reference


def test_alert_page_count_and_read_time_respect_scope_in_rest_and_stdio(live, team, actor):
    engine, api, _, _ = live
    project, _, users, _ = team
    pm, outsider = users['pm'], users['outsider']
    hidden = data(outsider.post('projects', headers=headers(), json={'name': 'Same tenant outside grant', 'code': 'ALERT-NO'}))['projectId']
    foreign, _ = actor()
    foreign_pid = data(api.post('/api/v1/projects', headers={**headers(), **bearer(foreign)},
        json={'name':'Other tenant', 'code':'ALERT-OTHER'}))['projectId']
    at = datetime(2026, 10, 3, 12, tzinfo=UTC)
    with engine.begin() as c:
        recent, ref = event(c, project, 'LR-08', '2026-09-30', at)
        previous, _ = event(c, project, 'LR-03', '2026-09', datetime(2026, 9, 30, 12, tzinfo=UTC))
        private, _ = event(c, hidden, 'LR-08', '2026-09-30', at)
        foreign_event, _ = event(c, foreign_pid, 'LR-08', '2026-09-30', at)

    request = 'analytics/health-alerts'
    project_path = f'projects/{project}/alerts'
    far = data(pm.get(request, params={'month': '2026-09', 'page': str(10**21), 'pageSize': 1}))
    assert far['total'] == 2 and far['items'] == [] and far['asOf']
    for n, expected in enumerate((recent, previous), start=1):
        response = pm.get(request, params={'month': '2026-09', 'page': n, 'pageSize': 1})
        page = data(response)
        row, = page['items']
        assert page['total'] == 2 and page['page'] == n and row['alertId'] == expected
        assert page['asOf'] == response.json()['meta']['asOf'] and page['asOf'] > row['asOf']
        assert response.json()['meta']['sourceRefs'] == [f'rule-event:{expected}']
        assert not any(unscoped in response.text for unscoped in (hidden, private, foreign_pid, foreign_event))
    assert data(pm.get(request, params={'month': '2026-10'}))['total'] == 0
    filtered = data(pm.get(request, params={'month': '2026-09', 'ruleId': 'LR-08'}))
    assert filtered['total'] == 1 and [row['alertId'] for row in filtered['items']] == [recent]
    assert data(outsider.get(request, params={'month': '2026-09'}))['items'][0]['alertId'] == private
    assert pm.get(f'projects/{hidden}/alerts').status_code == 404
    assert data(pm.get(project_path, params={'page': str(10**21)}))['total'] == 2

    token = issue(pm, project, ['pmo_alert_list'])
    rest = api.get('/api/v1/'+project_path, headers=bearer(token))
    agent = data(rest)
    human = data(pm.get(project_path))
    assert [item['alertId'] for item in human['items']] == [recent, previous]
    assert agent['total'] == human['total'] == 2
    assert set(agent) == {'items', 'page', 'pageSize', 'total', 'asOf'}
    assert agent['asOf'] == rest.json()['meta']['asOf']
    assert set(agent['items'][0]) == {'alertId', 'projectId', 'ruleId', 'severity', 'status', 'asOf', 'sourceRefs'}
    assert agent['items'][0]['sourceRefs'] == [ref] and agent['items'][0]['asOf'] == human['items'][0]['asOf']
    assert human['items'][0]['reason'] and 'reason' not in agent['items'][0] and 'projectCode' not in agent['items'][0]
    found, refused = mcp_calls(live, token, [('pmo_alert_list', {'projectId': project}), ('pmo_alert_list', {'projectId': hidden})])
    assert not found.isError and found.structuredContent['data']['items'] == agent['items']
    assert found.structuredContent['data']['asOf'] == found.structuredContent['meta']['asOf']
    assert refused.isError and refused.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND'
    denied = api.get('/api/v1/' + request, headers=bearer(token))
    assert denied.status_code == 403
    denied_audit(engine, denied, 'human_alerts_overview')
    with engine.connect() as c:
        logged = c.execute(sa.select(audit).where(audit.c.request_id == rest.headers['X-Request-ID'])).mappings().one()
        assert str(logged.project_id) == project and logged.source_refs == [f'rule-event:{recent}', f'rule-event:{previous}']


def test_alert_page_and_count_share_a_postgresql_snapshot(live, team, monkeypatch):
    engine = live[0]
    project, _, users, _ = team
    auth = Browser(users['pm'].cookies['pmo_session'], '', False)
    monkeypatch.setattr(projects, 'engine', lambda: engine)
    with engine.begin() as c:
        first, _ = event(c, project, 'LR-03', '2026-09', datetime(2026, 9, 30, 12, tzinfo=UTC))

    committed: list[str] = []

    def add_event_after_first_read(conn, cursor, statement, parameters, context, executemany):
        if committed or 'rule_events' not in statement or not statement.startswith(('SELECT', 'WITH')):
            return
        # The reader has obtained its snapshot. Commit a newer event via a separate connection.
        with engine.begin() as writer:
            new, _ = event(writer, project, 'LR-03', '2026-10', datetime(2026, 10, 1, 12, tzinfo=UTC))
        committed.append(new)

    sa.event.listen(engine, 'after_cursor_execute', add_event_after_first_read)
    try:
        before = alerts.listing(auth, UUID(project), 1, 1).data
    finally:
        sa.event.remove(engine, 'after_cursor_execute', add_event_after_first_read)
    assert committed and before.total == 1 and [str(row.alertId) for row in before.items] == [first]
    after = alerts.listing(auth, UUID(project), 1, 1).data
    assert after.total == 2 and [str(row.alertId) for row in after.items] == committed
