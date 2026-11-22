from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

from pmo import schema_v1 as db
from test_database_roles import serve
from test_lifecycle import propose, start_feasibility, upload
from test_reference_vectors_permissions import bearer, denied_audit
from test_reviews import bench, data, decide, headers, member, score, score_command, start_charter, team as team
from test_transition_authority import grant, revoke

PATH = 'me/approval-queue'


def another_project(engine, people, users, *, reviewer=True):
    pid = data(users['initiator'].post('projects', headers=headers(), json={
        'code': uuid4().hex, 'name': 'Second synthetic review'}))['projectId']
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(pm_id=people['pm'], sponsor_id=people['sponsor']))
        for role in ('pm', 'sponsor', 'reviewer' if reviewer else 'outsider'):
            member(c, pid, people[role], people['initiator'], role if role != 'outsider' else 'reviewer')
    return pid


def test_review_queue_counts_only_current_personal_actions(live, team, actor):
    engine, api, _, _ = live
    pid, people, users, _ = team
    pm, reviewer, sponsor, owner, outsider = (users[k] for k in ('pm', 'reviewer', 'sponsor', 'initiator', 'outsider'))
    unscored = start_charter(pid, users).split('/')[-1]
    scored_path = start_charter(another_project(engine, people, users), users)
    scored = score(reviewer, scored_path, [5] * 4)
    rid = scored['review']['reviewId']
    stale_pid = another_project(engine, people, users)
    stale_path = start_charter(stale_pid, users)
    not_assigned_pid = another_project(engine, people, users, reviewer=False)
    not_assigned = start_charter(not_assigned_pid, users)
    assert [row['reviewId'] for row in data(outsider.get(PATH))['items']] == [not_assigned.split('/')[-1]]
    admin_account = pm.get('auth/me').json()['accountId']
    with engine.begin() as c:
        c.execute(db.charters.update().where(db.charters.c.project_id == stale_pid).values(
            value_prop='Imported newer material', revision=db.charters.c.revision + 1))
        c.execute(db.user_roles.insert().values(user_id=admin_account, role='system_admin'))
        # Assigning a role after submission does not add a person to the frozen roster.
        member(c, not_assigned_pid, people['reviewer'], people['initiator'], 'reviewer')

    assert bench(reviewer, stale_path)['stale']
    for page, expected, action in [(1, rid, 'decide'), (2, unscored, 'score')]:
        response = reviewer.get(PATH, params={'page': page, 'pageSize': 1})
        payload = data(response)
        row, = payload['items']
        assert payload['total'] == 2 and payload['page'] == page
        assert row['kind'] == 'review' and row['reviewId'] == expected and row['actions'] == [action]
        assert row['submittedAt'] and response.json()['meta']['asOf']
        assert response.json()['meta']['sourceRefs'] == [f"review:{expected}@{row['revision']}"]
    assert data(reviewer.get(PATH, params={'page': str(10**21)}))['items'] == []
    assert data(pm.get(PATH, params={'reviewerId': str(people['reviewer'])}))['total'] == 0
    assert [row['actions'] for row in data(sponsor.get(PATH))['items']] == [['confirm_sponsor']]
    assert [row['actions'] for row in data(owner.get(PATH))['items']] == [['confirm_initiator']]
    assert bench(outsider, not_assigned)['stale'] and data(outsider.get(PATH))['total'] == 0

    for role, user in [('sponsor', sponsor), ('initiator', owner)]:
        data(user.post(scored_path + '/confirmations', headers=headers(bench(user, scored_path)['review']['revision']), json={'role': role}))
        assert data(user.get(PATH))['total'] == 0
    assert data(decide(reviewer, scored_path))['decision']['stage'] == 'designing'
    assert [row['reviewId'] for row in data(reviewer.get(PATH))['items']] == [unscored]

    token, _ = actor(extra_actions=('human_approval_queue',))
    denied = api.get('/api/v1/' + PATH, headers=bearer(token))
    assert (denied.status_code, denied.json()['code']) == (403, 'FORBIDDEN')
    denied_audit(engine, denied, 'human_approval_queue')


def test_stage_queue_checks_edge_authority_confirmation_and_policy(live, team, tmp_path):
    engine, _, _, _ = live
    pid, people, users, _ = team
    pm, reviewer, sponsor, owner, delegate = (users[k] for k in ('pm', 'reviewer', 'sponsor', 'initiator', 'outsider'))
    project, review = start_feasibility(live, team)
    prior = data(propose(pm, project, 'terminated'))
    score(reviewer, review, [3] * 5)  # Changes the frozen termination basis, not the project revision.
    req = data(propose(pm, project, 'terminated'))
    assert req['revision'] == prior['revision'] and req['requestId'] != prior['requestId']
    expected = req['requestId']
    row, = data(sponsor.get(PATH))['items']
    assert row['kind'] == 'transition' and row['approvalId'] == expected and row['actions'] == ['reject_transition']
    assert row['awaitsInitiator'] and row['project']['projectId'] == pid
    assert data(pm.get(PATH))['total'] == 0
    assert all(row['kind'] != 'transition' for row in data(reviewer.get(PATH))['items'])
    edge = ('feasibility_review', 'terminated')
    now = datetime.now(UTC)
    with engine.begin() as c:
        member(c, pid, people['outsider'], people['initiator'], 'member')
        grant(c, pid, people['outsider'], ('executing', 'terminated'))
        grant(c, pid, people['outsider'], edge, starts=now - timedelta(hours=2), expires=now - timedelta(hours=1))
        grant(c, pid, people['pm'], edge)
    assert data(delegate.get(PATH))['total'] == 0
    with engine.begin() as c:
        delegation = grant(c, pid, people['outsider'], edge)
    assert data(delegate.get(PATH))['items'] == [row]
    confirm = data(owner.post(f'approvals/{expected}/confirm-termination', headers=headers(req['revision']),
        json={'reason': 'I confirm the scope of synthetic termination.'}))
    assert confirm['initiatorConfirmedAt']
    assert data(sponsor.get(PATH))['items'][0]['actions'] == ['approve_transition', 'reject_transition']
    assert data(delegate.get(PATH))['items'][0]['actions'] == ['approve_transition', 'reject_transition']
    with engine.begin() as c:
        revoke(c, delegation)
    assert data(delegate.get(PATH))['total'] == 0 and data(pm.get(PATH))['total'] == 0

    exact = data(sponsor.get('approvals', params={'projectId': pid, 'approvalId': expected}))
    assert exact['total'] == 1 and [entry['requestId'] for entry in exact['items']] == [expected]
    other_pid = another_project(engine, people, users)
    assert data(sponsor.get('approvals', params={'projectId': other_pid, 'approvalId': expected}))['total'] == 0

    with serve(engine.url, tmp_path, extra_env={'PMO_SYNTHETIC_POLICY_SCHEMA': ''}) as restricted:
        restricted.cookies.update(sponsor.cookies)
        assert data(restricted.get(PATH))['total'] == 0


def test_invalid_score_attachment_removes_confirmations_and_decision(live, team):
    engine, _, _, env = live
    pid, _, users, _ = team
    pm, reviewer, sponsor, owner = (users[k] for k in ('pm', 'reviewer', 'sponsor', 'initiator'))
    path = start_charter(pid, users)
    rid = path.split('/')[-1]
    proof = upload(pm, reviewer, f'projects/{pid}', 'review', rid)
    download = f"evidence/{proof['evidenceId']}?download=true"
    assert owner.get(download).status_code == 404
    state = bench(reviewer, path)
    data(reviewer.post(path + '/scores', headers=headers(state['review']['revision']),
        json=score_command(state, [5] * 4, source=proof['evidenceRef'])))
    assert data(reviewer.get(PATH))['items'][0]['actions'] == ['decide']
    assert data(sponsor.get(PATH))['items'][0]['actions'] == ['confirm_sponsor']
    assert data(owner.get(PATH))['items'][0]['actions'] == ['confirm_initiator']
    with engine.connect() as c:
        key = c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id == proof['evidenceId']))
    source = Path(env['PMO_EVIDENCE_DIR']) / key
    original = source.read_bytes()
    try:
        source.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        assert bench(reviewer, path)['scoreEvidenceStale']
        for user in (reviewer, sponsor, owner):
            assert data(user.get(PATH))['total'] == 0
        refused = decide(reviewer, path, verdict='reject')
        assert refused.status_code == 409 and refused.json()['code'] == 'REVIEW_SNAPSHOT_STALE'
    finally:
        source.write_bytes(original)
    assert data(reviewer.get(PATH))['items'][0]['actions'] == ['decide']
    for role, user in [('sponsor', sponsor), ('initiator', owner)]:
        saved = data(user.post(path + '/confirmations', headers=headers(bench(user, path)['review']['revision']), json={'role': role}))
        assert any(row['role'] == role and row['current'] for row in saved['confirmations'])
    assert owner.get(download).status_code == 404
    assert data(decide(reviewer, path))['decision']['stage'] == 'designing'
