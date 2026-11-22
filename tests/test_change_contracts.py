import asyncio
import sys
from uuid import uuid4

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sqlalchemy as sa

from pmo import schema_v1 as db
from test_lifecycle import begin_execution
from test_reference_vectors_permissions import bearer, denied_audit, mcp_calls
from test_requirement_additions import COMMAND, grant_reviewers
from test_reviews import data, headers, issue, team as team


def checker(spec, path, method='get', *, request=False):
    route = spec['paths']['/api/v1/' + path][method]
    payload = route['requestBody'] if request else route['responses']['200']
    return validator({**payload['content']['application/json']['schema'], 'components': spec['components']})


def validator(schema):
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_change_submission_contract_at_http_and_stdio(live, team):
    engine, api, port, _ = live
    pid, _, users, _ = team
    pm = users['pm']
    project, req_id, _ = begin_execution(live, team)
    request_path = project + '/changes'
    requirement = data(pm.get(project + '/requirements'))['items'][0]
    milestone = data(pm.get(project + '/milestones'))['items'][0]
    charter = data(pm.get(project + '/charter'))['charter']
    with engine.connect() as c:
        budget = c.execute(sa.select(db.cost_entries.c.id, db.cost_entries.c.revision).where(
            db.cost_entries.c.project_id == pid, db.cost_entries.c.type == 'money')).one()

    def body(kind, target, before, after):
        return {'targetType': kind, 'targetId': str(target), 'before': before, 'after': after,
                'reason': 'Synthetic change to one approved baseline.'}

    cases = [
        (requirement['revision'], body('requirement', req_id, {'priority': 'P0'}, {'priority': 'P1'})),
        (milestone['revision'], body('milestone', milestone['milestoneId'], {'plannedDate': milestone['plannedDate']},
                                   {'plannedDate': '2027-01-05'})),
        (budget.revision, body('budget', budget.id, {'budget': '100.00'}, {'budget': '140.5'})),
        (charter['revision'], body('charter', charter['charterId'], {'topRisks': charter['topRisks']},
                                 {'topRisks': [{'statement': 'Late approval', 'response': 'Confirm new dates'}]})),
    ]
    invalid = [
        (cases[0][0], {**cases[0][1], 'after': {'priority': 'P1', 'status': 'cancelled'}}),
        (cases[0][0], {**cases[0][1], 'before': {'desc': requirement['desc']}}),
        (cases[1][0], {**cases[1][1], 'after': {'plannedDate': 1764547200}}),
        (cases[1][0], {**cases[1][1], 'after': {'forecastDate': '2027-01-05'}}),
        (cases[2][0], {**cases[2][1], 'after': {'budget': 140.5}}),
        (cases[3][0], {**cases[3][1], 'after': {'topRisks': [{'statement': 'Late approval',
                                                          'response': 'Confirm new dates', 'approvedBy': str(uuid4())}]}}),
        (cases[0][0], {**cases[0][1], 'before': {'present': False}}),
        (cases[0][0], {**cases[0][1], 'after': {}}),
    ]
    spec = api.get('/openapi.json').raise_for_status().json()
    post_schema = checker(spec, 'projects/{projectId}/changes', 'post', request=True)
    receipt_schema = checker(spec, 'projects/{projectId}/changes', 'post')
    list_schema = checker(spec, 'projects/{projectId}/changes')
    token = issue(pm, pid, ['pmo_change_propose', 'pmo_requirement_change_history'])
    rest = '/api/v1/' + request_path
    for revision, command in invalid:
        response = api.post(rest, headers={**bearer(token), **headers(revision)}, json=command)
        assert (response.status_code, response.json().get('code')) == (422, 'INVALID_CHANGE_FIELDS'), command
        denied_audit(engine, response, 'pmo_change_propose')
        assert not post_schema.is_valid(command), command
    assert data(pm.get(request_path))['total'] == 0
    with engine.connect() as c:
        assert c.execute(sa.text('''SELECT
            (SELECT count(*) FROM approval_requests WHERE project_id=:p AND action='baseline_change'),
            (SELECT count(*) FROM audit_logs WHERE project_id=:p AND action='pmo_change_propose' AND result='success')'''),
                         {'p': pid}).one() == (0, 0)

    params = StdioServerParameters(command=sys.executable, args=['-m', 'pmo.mcp_server'], env={
        'PMO_AGENT_TOKEN': token, 'PMO_API_PORT': str(port), 'PYTHONUTF8': '1'})

    async def scenario():
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tool = next(t for t in (await session.list_tools()).tools if t.name == 'pmo_change_propose')
            sdk_input, sdk_output = validator(tool.inputSchema), validator(tool.outputSchema)
            for revision, command in invalid:
                args = {**command, 'projectId': pid, 'expectedRevision': revision, 'idempotencyKey': uuid4().hex}
                assert not sdk_input.is_valid(args), command
                denied = await session.call_tool(tool.name, args)
                assert denied.isError and denied.structuredContent['error']['code'] == 'VALIDATION_FAILED'

            for revision, command in cases:
                assert post_schema.is_valid(command), command
                key = headers(revision)
                args = {**command, 'projectId': pid, 'expectedRevision': revision, 'idempotencyKey': key['Idempotency-Key']}
                sdk_input.validate(args)
                called = await session.call_tool(tool.name, args)
                assert not called.isError, called.content
                sdk_output.validate(called.structuredContent)
                response = api.post(rest, headers={**bearer(token), **key}, json=command)
                record = data(response)
                receipt_schema.validate(response.json())
                assert record['status'] == 'pending' and record['baseRevision'] == revision
                assert {name: record[name] for name in called.structuredContent['data']} == called.structuredContent['data']
                assert response.json()['meta'] == called.structuredContent['meta']
                malformed_record = {**record, 'after': {'approvedBy': str(uuid4())}}
                assert not receipt_schema.is_valid({**response.json(), 'data': malformed_record})
                malformed_sdk = {**called.structuredContent['data'], 'after': malformed_record['after']}
                assert not sdk_output.is_valid({**called.structuredContent, 'data': malformed_sdk})
                assert api.post(rest, headers={**bearer(token), **key}, json={**command, 'reason': 'Different request body'}).status_code == 409
                accept = '/api/v1/changes/' + record['changeId'] + '/approve'
                assert api.post(accept, headers={**bearer(token), **headers()}, json={'rationale': 'Cannot author and approve'}).status_code == 403

    asyncio.run(scenario())
    ledger = pm.get(request_path)
    list_schema.validate(ledger.json())
    reviewed = data(ledger)['items']
    assert len(reviewed) == 4 and all(row['status'] == 'pending' for row in reviewed)
    assert next(row for row in reviewed if row['targetType'] == 'budget')['after'] == {'budget': '140.50'}
    assert data(pm.get(project + '/milestones'))['items'][0] == milestone
    assert data(pm.get(project + '/requirements'))['items'][0] == requirement


def test_change_history_keeps_addition_and_opaque_legacy_snapshots(live, team):
    engine, api, _, _ = live
    pid, people, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    project, _, _ = begin_execution(live, team)
    grant_reviewers(live, team)
    revision = data(pm.get(project))['project']['revision']
    request = data(pm.post(project + '/changes/requirement-additions', headers=headers(revision), json=COMMAND))
    change = f"changes/{request['changeId']}/approve"
    receipt = reviewer.post(change, headers=headers(), json={'rationale': 'Reviewed the independent addition.'})
    row = data(receipt)
    spec = api.get('/openapi.json').raise_for_status().json()
    checker(spec, 'changes/{changeId}/approve', 'post').validate(receipt.json())
    assert row['before'] == request['before'] and row['after'] == request['after']
    assert set(row['before']) == {'present', 'initialBaselineRevision', 'initialBaselineHash'}
    req_id = row['targetId']
    path = f'requirements/{req_id}/changes'
    legacy_before, legacy_after = {'importNote': 'Opaque pre-contract field'}, None
    with engine.begin() as c:
        legacy_id = c.scalar(db.change_entries.insert().values(project_id=pid, target_type='requirement',
            target_id=req_id, base_revision=1, before=legacy_before, after=legacy_after, reason='Imported old entry',
            status='withdrawn', requested_by=people['pm'], created_by=people['pm']).returning(db.change_entries.c.id))
    history = pm.get(path)
    history_validator = checker(spec, 'requirements/{requirementId}/changes')
    history_validator.validate(history.json())
    entries = {item['changeId']: item for item in data(history)['items']}
    assert entries[row['changeId']] == row
    legacy = entries[str(legacy_id)]
    assert (legacy['legacySnapshot'], legacy['before'], legacy['after']) == (True, legacy_before, legacy_after)
    unchecked = {'items': [{key: value for key, value in legacy.items() if key != 'legacySnapshot'}]}
    assert not history_validator.is_valid({**history.json(), 'data': {**data(history), **unchecked}})
    assert users['outsider'].get(path).status_code == 404
    token = issue(pm, pid, ['pmo_requirement_change_history'])
    called, = mcp_calls(live, token, [('pmo_requirement_change_history', {'requirementId': req_id})])
    assert not called.isError, called.content
    mcp_rows = {item['changeId']: item for item in called.structuredContent['data']['items']}
    assert mcp_rows[row['changeId']]['before'] == row['before'] and mcp_rows[row['changeId']]['after'] == row['after']
    assert {'before', 'after'}.isdisjoint(mcp_rows[str(legacy_id)])
    with engine.connect() as c:
        assert c.execute(sa.select(db.change_entries.c.before, db.change_entries.c.after).where(
            db.change_entries.c.id == legacy_id)).one() == (legacy_before, legacy_after)
