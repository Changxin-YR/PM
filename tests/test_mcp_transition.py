import asyncio
from datetime import UTC, datetime, timedelta
import sys
from uuid import uuid4

import jsonschema
import sqlalchemy as sa
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pmo.project_lifecycle import REQUEST
from test_health import code
from test_lifecycle import execution_fixture, judgment, stage
from test_reviews import data, headers, issue, team as team


def test_stdio_transition_creates_pending_intent_and_cannot_approve(live, team):
    engine, api, port, _ = live
    pid, people, users, _ = team
    pm, sponsor = users['pm'], users['sponsor']
    project, _, _ = execution_fixture(live, team)
    revision = stage(pm, project)['revision']
    token = issue(pm, pid, actions=(REQUEST,))
    command = {'toStage': 'on_hold', 'reason': 'Wait for independently verified supplier access.'}
    attempt = headers(revision)
    agent = {'Authorization': f'Bearer {token}'}
    args = {**command, 'projectId': pid, 'expectedRevision': revision, 'idempotencyKey': attempt['Idempotency-Key']}

    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'pmo.mcp_server'], env={
            'PMO_API_PORT': str(port), 'PMO_AGENT_TOKEN': token, 'PYTHONUTF8': '1'})
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            tool = next(item for item in (await session.list_tools()).tools if item.name == REQUEST)
            check = jsonschema.Draft202012Validator(tool.outputSchema,
                                                    format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)

            async def call(**changes):
                result = await session.call_tool(REQUEST, {**args, **changes})
                check.validate(result.structuredContent)
                return result

            def denied(response, error):
                assert response.isError and response.structuredContent['error']['code'] == error

            denied(await call(projectId=str(uuid4())), 'RESOURCE_NOT_FOUND')
            denied(await call(toStage='closed', idempotencyKey=uuid4().hex), 'TRANSITION_NOT_ALLOWED')
            denied(await call(expectedRevision=revision - 1, idempotencyKey=uuid4().hex), 'VERSION_CONFLICT')
            first, second = await asyncio.gather(call(), call())
            assert not first.isError and not second.isError
            payload = first.structuredContent
            assert payload == second.structuredContent
            denied(await call(reason='Different payload with the same key.'), 'IDEMPOTENCY_CONFLICT')
            receipt = payload['data']
            rest = api.post(f'/api/v1/{project}/transition-requests', headers={**agent, **attempt}, json=command)
            assert rest.is_success and {'ok': True, **rest.json()} == payload
            assert receipt['fromStage'] == 'executing' and receipt['toStage'] == 'on_hold'
            assert receipt['status'] == 'pending' and receipt['revision'] == revision
            assert f"approval:{receipt['requestId']}" in payload['meta']['sourceRefs']
            assert stage(pm, project)['stage'] == 'executing'
            hold = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            path = f"approvals/{receipt['requestId']}/decide"
            decision = {'decision': 'approve', 'rationale': 'Check the independent request.', 'holdUntil': hold}
            code(api.post('/api/v1/' + path, headers={**agent, **headers(revision)}, json=decision), 403, 'FORBIDDEN')
            assert judgment(pm, receipt, holdUntil=hold).status_code == 403
            assert data(judgment(sponsor, receipt, holdUntil=hold))['status'] == 'applied'
            assert stage(pm, project)['stage'] == 'on_hold'

            issued, = data(pm.get('me/agent-tokens'))['items']
            token_path = f"me/agent-tokens/{issued['tokenId']}/revoke"
            assert data(pm.post(token_path, headers=headers(issued['revision']),
                                json={'reason': 'Synthetic verification finished.'}))['state'] == 'revoked'
            denied(await call(), 'UNAUTHORIZED')
            return receipt['requestId']

    request_id = asyncio.run(scenario())
    with engine.connect() as c:
        rows = c.execute(sa.text('''SELECT a.id,a.requested_by,a.delegated_by,i.actor_type
            FROM approval_requests a JOIN project_transition_intents i ON a.id=i.approval_id
            WHERE a.project_id=:p AND i.to_stage='on_hold' '''), {'p': pid}).all()
        assert [(str(r.id), r.requested_by, r.delegated_by, r.actor_type) for r in rows] == [
            (request_id, people['pm'], people['pm'], 'agent')]
        assert c.scalar(sa.text('''SELECT count(*) FROM audit_logs WHERE project_id=:p AND action=:op
            AND actor_type='agent' AND result='success' '''), {'p': pid, 'op': REQUEST}) == 1
        assert c.scalar(sa.text('''SELECT count(*) FROM outbox_events WHERE event_type=:op
            AND payload->>'approvalRequestId'=:req'''), {'op': REQUEST, 'req': request_id}) == 1
