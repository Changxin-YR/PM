"""All frozen tool entries exercise real stdio and server-side denied actions."""
from hashlib import sha256
from uuid import uuid4

import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.mcp_server import tools
from test_reference_vectors_permissions import mcp_calls
from test_reports import sections
from test_reviews import data, issue, team as team


def payload(name, pid, person):
    body = {
        **{f'{kind}Id': str(uuid4()) for kind in ('milestone', 'requirement', 'report', 'risk', 'review', 'task', 'target')},
        'projectId': pid, 'personId': str(person), 'code': 'PERMISSION-PROBE', 'name': 'Synthetic permission check',
        'idempotencyKey': uuid4().hex, 'expectedRevision': 1, 'fields': {'name': 'Attempted update'},
        'role': 'pm', 'fieldsCharter': {'valueProp': 'Synthetic test value'}, 'gate': 'charter', 'reviewType': 'charter',
        'plannedDate': '2026-12-01', 'fieldsMilestone': {'name': 'Attempted change'}, 'newDate': '2026-12-05',
        'reason': 'Synthetic permission check only', 'desc': 'Synthetic scope validation', 'priority': 'P1',
        'acceptanceCriteria': 'Verification uses a synthetic fixture', 'fieldsRequirement': {'desc': 'Attempted request'},
        'evidenceRef': f'evidence:{uuid4()}', 'targetType': 'milestone',
        'before': {'plannedDate': '2026-12-01'}, 'after': {'plannedDate': '2026-12-05'}, 'type': 'money',
        'amount': '8.00', 'budget': '100.00', 'forecastRemaining': '10.00', 'period': '2026-01',
        'sourceRef': 'synthetic:immutable-test-source', 'statement': 'Synthetic condition only', 'prob': 2, 'impact': 2,
        'response': 'Check after approval', 'fieldsRisk': {'prob': 3}, 'fieldsTask': {'status': 'done'},
        'reportType': 'weekly', 'structuredFields': sections(), 'quote': 'Synthetic receipt exists',
        'typedValue': 'synthetic', 'subject': 'Synthetic report', 'predicate': 'sample', 'asOf': '2026-01-10T00:00:00Z',
        'stakeholderType': 'internal', 'toStage': 'delivering',
    }
    if name == 'pmo_report_submit':
        body['period'] = '2026-W04'
    return {field: body[field] for field in tools[name].inputSchema.get('required', [])}


def test_all_40_tools_deny_ungranted_action_and_reject_unknown_input(live, team):
    engine = live[0]
    pid, people, users, _ = team
    pm = users['pm']
    first = issue(pm, pid, ['pmo_project_list'])
    second = issue(pm, pid, ['pmo_project_get'])
    identities = [sha256(value.encode()).hexdigest() for value in (first, second)]
    cases = [(name, payload(name, pid, people['reviewer'])) for name in sorted(tools)]
    assert len(cases) == 40
    # Count every frozen business table, plus writes to outbox/idempotency, in this isolated schema.
    tables = [table for table in db.metadata.tables.values() if table is not db.audit_logs]

    def counts(c):
        return [c.scalar(sa.select(sa.func.count()).select_from(table)) for table in tables]

    with engine.connect() as c:
        ids = c.scalars(sa.select(db.agent_credentials.c.id).where(db.agent_credentials.c.token_hash.in_(identities))).all()
        assert len(ids) == 2
        before = counts(c)
    allowed, = mcp_calls(live, first, [('pmo_project_list', {})])
    assert not allowed.isError and allowed.structuredContent['data']['items'][0]['projectId'] == pid
    for token, checked in [(first, [case for case in cases if case[0] != 'pmo_project_list']),
                           (second, [('pmo_project_list', {})])]:
        denied = mcp_calls(live, token, checked)
        for (name, _), result in zip(checked, denied, strict=True):
            assert result.isError and result.structuredContent['error']['code'] == 'FORBIDDEN', name
    invalid = [(name, {**body, 'requestedRole': 'admin'}) for name, body in cases]
    for (name, _), result in zip(invalid, mcp_calls(live, first, invalid), strict=True):
        assert result.isError and result.structuredContent['error']['code'] == 'VALIDATION_FAILED', name
    with engine.connect() as c:
        assert counts(c) == before
        audit = db.audit_logs.c
        rows = c.execute(sa.select(audit.action, audit.result, audit.error_code).where(audit.actor_id.in_(ids), audit.result == 'denied')).all()
        assert sorted(rows) == [(name, 'denied', 'FORBIDDEN') for name in sorted(tools)]
    assert data(pm.get(f'projects/{pid}'))['project']['revision'] == 1
