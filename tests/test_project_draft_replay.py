"""History replay is authorization-checked even after a draft stops being editable."""
import sqlalchemy as sa

from pmo import schema_v1 as db
from test_health import code
from test_lifecycle import stage
from test_reference_vectors_permissions import bearer, denied_audit, mcp_calls
from test_reviews import data, decide, headers, issue, score, start_charter, team as team


def test_draft_update_replay_after_submission_preserves_live_authority(live, team):
    engine, api, _, _ = live
    pid, _, users, _ = team
    owner, reviewer = users['initiator'], users['reviewer']
    path = f'projects/{pid}'
    operation = 'pmo_project_draft_update'
    token = issue(owner, pid, [operation])
    human_key = headers(stage(owner, path)['revision'])
    human_payload = {'fields': {'description': 'Synthetic scope summary.'}}
    original = owner.patch(path, headers=human_key, json=human_payload)
    human_result = data(original)

    mcp_args = {'projectId': pid, 'idempotencyKey': human_key['Idempotency-Key'],
                'expectedRevision': human_result['revision'], 'fields': {'description': 'Scoped Agent draft edit.'}}
    agent, = mcp_calls(live, token, [(operation, mcp_args)])
    assert not agent.isError and agent.structuredContent['data']['revision'] == human_result['revision'] + 1
    agent_key = {**bearer(token), 'If-Match': str(mcp_args['expectedRevision']), 'Idempotency-Key': mcp_args['idempotencyKey']}
    agent_payload = {'fields': mcp_args['fields']}

    review = start_charter(pid, users)
    score(reviewer, review, [4] * 4)
    assert data(decide(reviewer, review))['decision']['stage'] == 'designing'
    frozen = stage(owner, path)
    replayed = owner.patch(path, headers=human_key, json=human_payload)
    assert (replayed.status_code, replayed.json()) == (original.status_code, original.json())
    rest = api.patch('/api/v1/' + path, headers=agent_key, json=agent_payload)
    assert {**rest.json(), 'ok': True} == agent.structuredContent and rest.status_code == 200
    same, = mcp_calls(live, token, [(operation, mcp_args)])
    assert not same.isError and same.structuredContent == agent.structuredContent

    conflict = owner.patch(path, headers=human_key, json=agent_payload)
    code(conflict, 409, 'IDEMPOTENCY_CONFLICT')
    denied_audit(engine, conflict, operation)
    new_write = owner.patch(path, headers=headers(frozen['revision']), json=human_payload)
    code(new_write, 403, 'FORBIDDEN')
    denied_audit(engine, new_write, operation)
    not_owner = reviewer.patch(path, headers=human_key, json=human_payload)
    code(not_owner, 403, 'FORBIDDEN')
    denied_audit(engine, not_owner, operation)

    issued, = [row for row in data(owner.get('me/agent-tokens'))['items'] if row['allowedActions'] == [operation]]
    data(owner.post(f"me/agent-tokens/{issued['tokenId']}/revoke", headers=headers(issued['revision']),
                    json={'reason': 'End the synthetic delegation.'}))
    denied = api.patch('/api/v1/' + path, headers=agent_key, json=agent_payload)
    code(denied, 401, 'UNAUTHORIZED')
    revoked, = mcp_calls(live, token, [(operation, mcp_args)])
    assert revoked.isError and revoked.structuredContent['error']['code'] == 'UNAUTHORIZED'
    assert stage(owner, path) == frozen
    with engine.connect() as c:
        receipt = db.idempotency_keys.c
        assert set(c.execute(sa.select(receipt.actor_type, receipt.response_status).where(
            receipt.resource_id == pid, receipt.operation == operation, receipt.idempotency_key == human_key['Idempotency-Key']))) == {
                ('human', 200), ('agent', 200)}
        audit, outbox = db.audit_logs.c, db.outbox_events.c
        assert c.scalar(sa.select(sa.func.count()).where(audit.project_id == pid, audit.action == operation,
                                                        audit.result == 'success')) == 2
        assert c.scalar(sa.select(sa.func.count()).where(outbox.event_type == 'project.updated',
                                                        outbox.payload['projectId'].astext == pid)) == 2
