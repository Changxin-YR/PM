"""Same-organization resource scope and authorization transitions against PostgreSQL."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import re

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.identity import explicit_grants
from test_evidence import binary
from test_facts import post as propose_fact, submit as submit_report
from test_reports import body, data, deny, headers, team as team
from test_reviews import issue


def proof(client, project, kind, resource, revision):
    return client.post(f'projects/{project}/evidence', headers=headers(revision),
        data={'linkedResourceType': kind, 'linkedResourceId': str(resource)},
        files={'file': ('synthetic.png', binary('png'), 'image/png')})


def test_evidence_paging_and_revision_exclude_other_authors_and_projects(live, team):
    pm, member = (team.users[role] for role in ('pm', 'reporter'))
    outsider = team.users['outsider']  # Same tenant, member of the other project only.
    project = f'projects/{team.pid}'
    evidence = project + '/evidence'
    for suffix in ('evidence', 'closing/check', 'requirement-metrics', 'requirement-exceptions'):
        deny(outsider.get(f'{project}/{suffix}'), 404, 'RESOURCE_NOT_FOUND')
    deny(outsider.post(team.path + '/drafts', headers=headers(), json=body()), 404, 'RESOURCE_NOT_FOUND')
    private = data(pm.post(team.path + '/drafts', headers=headers(), json=body()))
    other = data(pm.post(f'projects/{team.other}/reports/drafts', headers=headers(), json=body()))
    initial = data(member.get(evidence))
    denied = proof(pm, team.pid, 'report', other['reportId'], 1)
    deny(denied, 404, 'RESOURCE_NOT_FOUND')
    visible_to_pm = data(proof(pm, team.pid, 'report', private['reportId'], 1))
    assert data(pm.get(evidence, params={'reportId': private['reportId']}))['total'] == 1
    assert data(pm.get(evidence, params={'reportId': other['reportId']}))['total'] == 0
    # An opaque-looking collection counter must not reveal another author's attachment.
    member_after = data(member.get(evidence, params={'reportId': private['reportId']}))
    assert member_after['items'] == [] and member_after['total'] == 0
    assert member_after['collectionRevision'] == initial['collectionRevision']
    file_path = f"evidence/{visible_to_pm['evidenceId']}"
    for params in ({}, {'download': 'true'}):
        deny(member.get(file_path, params=params), 404, 'RESOURCE_NOT_FOUND')
    deny(member.get(f"reports/{private['reportId']}/facts"), 404, 'RESOURCE_NOT_FOUND')
    assert data(pm.get(f"reports/{private['reportId']}/facts"))['total'] == 0
    own = data(member.post(team.path + '/drafts', headers=headers(), json=body()))
    own_file = data(proof(member, team.pid, 'report', own['reportId'], initial['collectionRevision']))
    assert data(member.get(evidence))['items'][0]['evidenceId'] == own_file['evidenceId']
    assert data(pm.get(evidence))['total'] == 2
    assert len(list(Path(live[3]['PMO_EVIDENCE_DIR']).glob('*'))) >= 2
    with live[0].connect() as c:
        rows = c.execute(sa.select(db.evidence_files.c.id).where(db.evidence_files.c.project_id == team.pid)).scalars().all()
        assert set(map(str, rows)) == {own_file['evidenceId'], visible_to_pm['evidenceId']}


def test_large_pages_are_empty_with_authorized_totals(live, team):
    pm = team.users['pm']
    p = f'projects/{team.pid}'
    with live[0].begin() as c:
        c.execute(db.user_roles.insert().values(user_id=team.people['pm'].account, role='system_admin'))
        requirement = c.scalar(db.requirements.insert().values(
            project_id=team.pid, created_by=team.people['pm'].id, desc='Synthetic pagination boundary',
            priority='P1', acceptance_criteria='Retain authorized totals', status='baseline',
            baseline_version=1).returning(db.requirements.c.id))
    spec = live[1].get('/openapi.json').json()['paths']
    report = submit_report(pm, team.path)
    report_path = f"reports/{report['reportId']}"
    fact = data(propose_fact(pm, report))
    data(pm.post(report_path + '/confirm', headers=headers(report['revision']), json={}))
    covered = set()
    for path in ('projects', 'project-overview', 'onboarding/projects', 'onboarding/people',
                 'analytics/weekly-health', 'analytics/weekly-reports', 'analytics/health-alerts',
                 'me/approval-queue', 'me/strengthening-tasks', 'me/agent-tokens', 'admin/agents/tokens',
                 'admin/audits', 'admin/users', 'admin/members', 'admin/users/invitations',
                 'analytics/preapproval-spending', 'lessons/search', report_path + '/facts', report_path + '/analysis-runs',
                 report_path + '/facts/weekly-comparison', f'{p}/changes/targets',
                 'approvals', f'requirements/{requirement}/disputes', f'requirements/{requirement}/changes',
                 f"facts/{fact['factId']}/history",
                 *(f'{p}/{suffix}' for suffix in ('members', 'evidence', 'health/history', 'cost-postings',
                       'requirements', 'risks', 'stakeholders', 'reports', 'tasks', 'alerts', 'changes'))):
        filters = {'projectId': str(team.pid)} if path == 'approvals' else {}
        if path == 'onboarding/people':
            filters = {'keyword': 'reviewer'}
        if path == f'{p}/changes/targets':
            filters = {'kind': 'milestone'}
        current = pm.get(path, params={**filters, 'pageSize': 1})
        assert current.status_code == 200, (path, current.text)
        if path.startswith('onboarding/'):
            assert current.json()['data']['total'] > 0, path
        far = pm.get(path, params={**filters, 'page': '100000000000000000000', 'pageSize': 1})
        template = re.sub(r'\b[0-9a-f]{8}-[0-9a-f-]{27}\b', r'[^/]+', '/api/v1/' + path)
        shape = next(route['get'] for key, route in spec.items() if 'get' in route and re.fullmatch(template, key))
        covered.add(shape['operationId'])
        page = next(param['schema'] for param in shape['parameters'] if param['name'] == 'page')
        if 'maximum' in page:
            deny(far, 422, 'VALIDATION_FAILED')
            far = pm.get(path, params={**filters, 'page': int(page['maximum']), 'pageSize': 1})
        assert far.status_code == 200, (path, far.text)
        assert far.json()['data']['total'] == current.json()['data']['total'], path
        assert not far.json()['data']['items'], path
    assert covered == {
        route['get']['operationId'] for route in spec.values()
        if any(parameter['name'] == 'page' for parameter in route.get('get', {}).get('parameters', ()))
    }


@pytest.mark.parametrize('loss', ['revoke', 'expire', 'delegator', 'member', 'scope'])
def test_current_delegation_precedes_resource_and_idempotency(live, team, loss):
    engine, api, _, _ = live
    pm = team.users['pm']
    p = f'/api/v1/projects/{team.pid}'
    roles, members, tokens = db.user_roles, db.project_members, db.agent_credentials
    account = team.people['pm'].account
    with engine.begin() as c:
        c.execute(roles.insert().values(user_id=account, role='delegator'))
    actions = ('pmo_project_get', 'pmo_risk_create')
    verify = issue(pm, str(team.other), actions)
    token = issue(pm, str(team.pid), actions)
    auth = {'Authorization': 'Bearer ' + token}
    receipt = next(x for x in data(pm.get('me/agent-tokens'))['items'] if x['allowedProjectIds'] == [str(team.pid)])
    path = p + '/risks'
    command = {'statement': 'Synthetic risk prepared before the authorization change', 'prob': 2, 'impact': 2,
               'response': 'Record a control at the next independent review'}
    key = {**headers(1), **auth}
    created = api.post(path, headers=key, json=command)
    assert data(created)['revision'] == 1
    assert api.post(path, headers=key, json=command).json() == created.json()
    deny(api.post(path, headers=key, json={**command, 'prob': 3}), 409, 'IDEMPOTENCY_CONFLICT')
    deny(api.get(f'/api/v1/projects/{team.other}', headers=auth), 404, 'RESOURCE_NOT_FOUND')
    if loss == 'revoke':
        data(pm.post(f"me/agent-tokens/{receipt['tokenId']}/revoke", headers=headers(receipt['revision']),
                     json={'reason': 'Synthetic end of authorization'}))
    else:
        with engine.begin() as c:
            if loss == 'expire':
                now = c.scalar(sa.select(sa.func.now()))
                c.execute(tokens.update().where(tokens.c.id == receipt['tokenId']).values(
                    created_at=now - timedelta(minutes=2), expires_at=now - timedelta(minutes=1)))
            elif loss == 'delegator':
                c.execute(roles.delete().where(roles.c.user_id == account, roles.c.role == 'delegator'))
            elif loss == 'member':
                c.execute(members.update().where(members.c.project_id == team.pid, members.c.person_id == team.people['pm'].id)
                    .values(revoked_at=sa.func.now()))
            else:
                c.execute(explicit_grants.delete().where(explicit_grants.c.credential_id == receipt['tokenId']))
    expected = (404, 'RESOURCE_NOT_FOUND') if loss in ('member', 'scope') else (401, 'UNAUTHORIZED')
    deny(api.get(p, headers=auth), *expected)
    deny(api.post(path, headers=key, json=command), *expected)
    deny(api.post(path, headers={**headers(1), **auth}, json=command), *expected)
    control = api.get(f'/api/v1/projects/{team.other}', headers={'Authorization': 'Bearer ' + verify})
    assert control.status_code == (401 if loss == 'delegator' else 200)
    with engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(db.risks).where(db.risks.c.project_id == team.pid)) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(db.idempotency_keys).where(
            db.idempotency_keys.c.idempotency_key == key['Idempotency-Key'])) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(db.audit_logs).where(db.audit_logs.c.project_id == team.pid,
            db.audit_logs.c.action == 'pmo_risk_create', db.audit_logs.c.result == 'success')) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(db.outbox_events).where(
            db.outbox_events.c.dedupe_key == created.headers['X-Request-ID'])) == 1


def test_session_only_profile_and_public_probe_boundaries(live, team, actor):
    _, api, _, _ = live
    token, _ = actor()
    bearer = {'Authorization': f'Bearer {token}'}
    deny(api.get('/api/v1/auth/me', headers=bearer), 403, 'FORBIDDEN')
    deny(api.post('/api/v1/auth/login', headers=bearer, json={'username': team.people['pm'].username,
        'password': 'Not the local test credential'}), 403, 'FORBIDDEN')
    for role in ('pm', 'outsider'):
        who = team.users[role].get('auth/me').raise_for_status().json()
        assert who == {'accountId': str(team.people[role].account), 'personId': str(team.people[role].id),
                       'orgId': str(team.people[role].org), 'isSystemAdmin': False}
    for path, status in (('/health/live', 'live'), ('/health/ready', 'ready')):
        result = api.get(path)
        assert result.status_code == 200 and result.json() == {'status': status}
