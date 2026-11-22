import sqlalchemy as sa

from pmo import schema_v1 as db
from test_reference_vectors_permissions import bearer, denied_audit
from test_tasks import command, change, denied, headers, ok, project, team as team, PROJECT_REVISION


QUEUE = 'me/strengthening-tasks'


def test_my_strengthening_tasks_filter_before_count_and_pagination(live, team, actor):
    engine, api, _, _ = live
    people, users = team
    pm, owner, peer = (users[role] for role in ('pm', 'owner', 'peer'))
    first, second, private = (project(engine, people) for _ in range(3))
    archived = project(engine, people, stage='archived')
    foreign = project(engine, people, foreign=True)
    assigned = ok(pm.post(first.path, headers=headers(PROJECT_REVISION), json=command(first, 'rule_event',
        assigneeId=str(people['owner'].id), dueAt='2027-01-10T16:00:00+08:00')))
    another = ok(pm.post(second.path, headers=headers(PROJECT_REVISION), json=command(second, 'remediation',
        assigneeId=str(people['owner'].id), dueAt='2027-01-12T16:00:00+08:00')))
    hidden = ok(pm.post(first.path, headers=headers(PROJECT_REVISION), json=command(first, assigneeId=str(people['peer'].id))))
    assert ok(change(owner, first.system, {'status': 'done'}))['status'] == 'done'
    assert ok(change(owner, another['taskId'], {'status': 'blocked'}))['status'] == 'blocked'
    with engine.begin() as c:
        c.execute(db.project_members.update().where(db.project_members.c.project_id == private.id,
            db.project_members.c.person_id == people['owner'].id).values(revoked_at=sa.func.now()))

    seen = []
    for n, task_id in enumerate((assigned['taskId'], another['taskId'], second.system), start=1):
        response = owner.get(QUEUE, params={'page': n, 'pageSize': 1})
        payload = ok(response)
        row, = payload['items']
        assert payload['total'] == 3 and payload['page'] == n
        task = row['task']
        assert task['taskId'] == task_id and task['assigneeId'] == str(people['owner'].id)
        assert task['canUpdate'] and task['sourceId']
        assert row['project']['projectId'] == task['projectId']
        assert response.json()['meta']['asOf']
        assert response.json()['meta']['sourceRefs'] == [f"task:{task_id}@{task['revision']}"]
        assert not any(str(p.id) in response.text for p in (private, archived, foreign))
        seen.append(task['status'])
    assert seen == ['todo', 'blocked', 'todo']
    beyond = ok(owner.get(QUEUE, params={'page': str(10**21), 'pageSize': 1}))
    assert beyond['items'] == [] and beyond['total'] == 3
    assert ok(owner.get(QUEUE, params={'assigneeId': str(people['peer'].id)}))['total'] == 3
    assert ok(owner.get(first.path, params={'taskId': assigned['taskId']}))['items'][0]['taskId'] == assigned['taskId']
    assert ok(owner.get(first.path, params={'taskId': hidden['taskId']}))['items'] == []
    assert ok(owner.get(first.path, params={'taskId': another['taskId']}))['items'] == []
    denied(owner.get(private.path, params={'taskId': private.system}), 404, 'RESOURCE_NOT_FOUND')
    assert [r['task']['taskId'] for r in ok(peer.get(QUEUE))['items']] == [hidden['taskId']]
    assert ok(pm.get(QUEUE))['total'] == 0

    agent, _ = actor(extra_actions=('human_strengthening_queue',))
    refused = api.get('/api/v1/' + QUEUE, headers=bearer(agent))
    denied(refused, 403, 'FORBIDDEN')
    denied_audit(engine, refused, 'human_strengthening_queue')
    assert ok(change(owner, assigned['taskId'], {'status': 'done'}))['status'] == 'done'
    assert ok(owner.get(QUEUE))['total'] == 2
