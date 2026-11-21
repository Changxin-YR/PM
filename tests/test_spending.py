"""Verified posting occurrence, not verification time, defines preapproval spending."""
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from pmo import schema_v1 as db
from test_reference_vectors_permissions import mcp_calls
from test_reviews import data, decide, headers, issue, score, start_charter, team as team


def test_preapproval_spending_recognizes_conditional_charter_decision(live, team):
    pid, people, users, _ = team
    pm, reviewer, outsider = (users[key] for key in ('pm', 'reviewer', 'outsider'))
    with live[0].begin() as c:
        account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == people['reviewer']))
        c.execute(db.user_roles.insert().values(user_id=account, role='finance_verifier'))

    def post(kind, amount, when, ref, *, verify=True):
        saved = data(pm.post(f'projects/{pid}/cost-postings', headers=headers(), json={'type': kind, 'amount': amount,
            'period': when.strftime('%Y-%m'), 'sourceRef': ref}))
        if verify:
            return data(reviewer.post(f"cost-postings/{saved['postingId']}/verify", headers=headers(),
                json={'reason': 'Checked original synthetic source.', 'occurredAt': when.isoformat()}))
        return saved

    path = 'analytics/preapproval-spending'
    date_before = datetime(2025, 1, 10, tzinfo=UTC)
    before = post('money', '13.25', date_before, 'Historical purchase')
    post('money', '44', date_before, 'Unverified candidate', verify=False)
    old = data(pm.get(path))['items']
    assert [row['postingId'] for row in old] == [before['postingId']]
    assert 'approvedAt' not in old[0] and old[0]['amountSigned'] == '13.25'
    review = start_charter(pid, users)
    score(reviewer, review, [4] * 4)
    decision = data(decide(reviewer, review, verdict='conditional_pass', conditions=[{
        'title': 'Confirm delivery material before planning', 'assigneeId': str(people['pm']),
        'dueAt': (datetime.now(UTC) + timedelta(days=5)).isoformat()}]))['decision']
    assert decision['stage'] == 'designing'
    # Both are verified after approval. Only the receipt actually incurred before approval qualifies.
    late_verified = post('effort', '2.75', date_before, 'Prior work log')
    post('money', '7', datetime.now(UTC), 'Work after approval')
    response = pm.get(path).json()
    assert {row['postingId'] for row in response['data']['items']} == {before['postingId'], late_verified['postingId']}
    assert all(row['approvedAt'] == decision['decidedAt'] for row in response['data']['items'])
    assert f"decision:{decision['decisionId']}" in response['meta']['sourceRefs']
    assert data(outsider.get(path))['total'] == 0
    token = issue(pm, pid, ['pmo_preapproval_spending_list'])
    same, = mcp_calls(live, token, [('pmo_preapproval_spending_list', {})])
    assert not same.isError and same.structuredContent['data']['items'] == response['data']['items']
