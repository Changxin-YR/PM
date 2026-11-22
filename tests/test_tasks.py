import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from queue import Queue
from secrets import token_urlsafe
import sys
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

from argon2 import PasswordHasher
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.contracts import RULE_VERSION
from pmo.identity import explicit_grants
from pmo.projects import fingerprint
from pmo.review_outcomes import strengthen
from pmo.tasks import CREATE, LIST, UPDATE

PROJECT_REVISION = 7
DUE = '2027-01-16T14:00:00+08:00'


def headers(revision):
    return {'If-Match': str(revision), 'Idempotency-Key': uuid4().hex}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def denied(response, status, code=None):
    assert response.status_code == status, response.text
    if code is not None:
        assert response.json()['code'] == code


def make_person(c, org, name):
    person, username, password = uuid4(), uuid4().hex, token_urlsafe(24)
    account = c.scalar(db.user_accounts.insert().values(username=username, is_active=True,
        password_hash=PasswordHasher(time_cost=1, memory_cost=8192).hash(password)).returning(db.user_accounts.c.id))
    c.execute(db.persons.insert().values(id=person, user_id=account, created_by=person, org_id=org, name=name, is_active=True))
    c.execute(db.user_roles.insert().values(user_id=account, role='delegator'))
    return SimpleNamespace(id=person, org=org, account=account, username=username, password=password)


@pytest.fixture
def team(live):
    engine, _, port, _ = live
    with engine.begin() as c:
        org, other_org = [c.scalar(db.organizations.insert().values(name='Task test', status='active')
                                  .returning(db.organizations.c.id)) for _ in range(2)]
        people = {name: make_person(c, other_org if name == 'foreign' else org, name)
                  for name in ('pm', 'owner', 'peer', 'reviewer', 'pending', 'foreign')}
    with ExitStack() as stack:
        sessions = {}
        for name, person in people.items():
            client = stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/', trust_env=False))
            login = client.post('auth/login', json={'username': person.username, 'password': person.password})
            assert login.is_success, login.text
            client.headers['X-CSRF-Token'] = login.json()['csrfToken']
            sessions[name] = client
        yield people, sessions


def project(engine, people, *, foreign=False, stage='feasibility_review'):
    pm = people['foreign' if foreign else 'pm']
    reviewer = pm if foreign else people['reviewer']
    owner = pm if foreign else people['owner']
    memberships = [(pm, 'pm', 'accepted')]
    if not foreign:
        memberships += [(owner, 'member', 'accepted'), (people['peer'], 'member', 'accepted'),
                        (reviewer, 'reviewer', 'accepted'), (people['pending'], 'member', 'pending')]
    with engine.begin() as c:
        pid = c.scalar(db.projects.insert().values(code=uuid4().hex, org_id=pm.org, name='Task project',
            pm_id=pm.id, created_by=pm.id, initiator_id=pm.id, stage='feasibility_review' if stage == 'archived' else stage,
            revision=PROJECT_REVISION).returning(db.projects.c.id))
        for person, role, assignment in memberships:
            c.execute(db.project_members.insert().values(project_id=pid, person_id=person.id, assigned_by=pm.id,
                role=role, assignment_status=assignment, accepted_at=sa.func.now() if assignment == 'accepted' else None))
        digest = fingerprint([str(pid), 'Synthetic review snapshot'])
        review = c.scalar(db.reviews.insert().values(project_id=pid, created_by=pm.id, type='feasibility',
            status='pending', snapshot_hash=digest, round=1, attendees=[]).returning(db.reviews.c.id))
        c.execute(sa.text('''INSERT INTO review_submission_snapshots(review_id, rule_version, snapshot_hash, materials, source_refs)
            VALUES (:id, :rule, :digest, jsonb_build_object('reviewers', jsonb_build_array(CAST(:reviewer AS text))), '[]')'''),
            dict(id=review, rule=RULE_VERSION, digest=digest, reviewer=reviewer.id))
        # Use the production review writer, including its strengthening-task cross-reference.
        [system_task] = strengthen(c, SimpleNamespace(person=reviewer.id), SimpleNamespace(id=pid),
            SimpleNamespace(id=review), [('dimension:resources', 'System strengthening: resources', owner.id, None)])
        rule = c.scalar(db.rule_events.insert().values(project_id=pid, rule_id='LR-03', rule_version=RULE_VERSION,
            business_period='2026-11', fingerprint=digest, source_refs=[], status='open').returning(db.rule_events.c.id))
        risk = c.scalar(db.risks.insert().values(project_id=pid, created_by=pm.id, owner_id=pm.id,
            statement='Remediation risk', response='Track the risk', is_blocking=False, prob=2, impact=2,
            status='open').returning(db.risks.c.id))
        if stage == 'archived':
            c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage=stage))
    return SimpleNamespace(id=pid, path=f'projects/{pid}/tasks', system=system_task,
                           sources={'review': review, 'rule_event': rule, 'remediation': risk})


def command(project, source='review', **fields):
    return {'title': 'Verify alternate supplier', 'sourceType': source, 'sourceId': str(project.sources[source]), **fields}


def change(client, task_id, fields, revision=1, **kwargs):
    return client.patch(f'tasks/{task_id}', headers=kwargs.pop('headers', headers(revision)), json={'fieldsTask': fields}, **kwargs)


def issue(client, actions, projects, *, allow_create=False):
    info = ok(client.get('me/agent-tokens'))
    return client.post('me/agent-tokens', headers=headers(info['collectionRevision']),
        json={'allowedActions': actions, 'allowedProjectIds': list(map(str, projects)), 'allowProjectCreate': allow_create,
              'expiresAt': (datetime.now(UTC) + timedelta(hours=1)).isoformat()})


def test_human_task_and_existing_system_task(live, team):
    engine, _, _, _ = live
    people, clients = team
    pm, owner = clients['pm'], clients['owner']
    p = project(engine, people)
    listing = ok(pm.get(p.path))
    assert listing['canCreate'] and listing['projectRevision'] == PROJECT_REVISION and listing['total'] == 1
    assert [(x['taskId'], x['status'], x['sourceType']) for x in listing['items']] == [(p.system, 'todo', 'review')]
    body = command(p, 'rule_event', assigneeId=str(people['owner'].id), dueAt=DUE)
    h = headers(PROJECT_REVISION)
    denied(pm.post(p.path, headers={'Idempotency-Key': uuid4().hex}, json=body), 422, 'VALIDATION_FAILED')
    denied(pm.post(p.path, headers=headers(PROJECT_REVISION-1), json=body), 409, 'VERSION_CONFLICT')
    denied(pm.post(p.path, headers={**h, 'X-CSRF-Token': ''}, json=body), 403, 'CSRF_INVALID')
    denied(owner.post(p.path, headers=h, json=body), 403, 'FORBIDDEN')
    first = pm.post(p.path, headers=h, json=body)
    task = ok(first)
    assert task['status'] == 'todo' and task['revision'] == 1 and task['canUpdate']
    assert task['sourceId'] == body['sourceId'] and task['assigneeId'] == body['assigneeId']
    assert datetime.fromisoformat(task['dueAt']) == datetime.fromisoformat(DUE)
    assert pm.post(p.path, headers=h, json=body).json() == first.json()
    denied(pm.post(p.path, headers=h, json={**body, 'title': 'Changed'}), 409, 'IDEMPOTENCY_CONFLICT')
    assert ok(pm.get(p.path+'?page=2&pageSize=1'))['items'][0]['taskId'] == p.system
    assert ok(owner.get(p.path))['total'] == 2 and not ok(owner.get(p.path))['canCreate']
    assert ok(clients['peer'].get(p.path))['total'] == 0
    assert all(not row['canUpdate'] for row in ok(clients['reviewer'].get(p.path))['items'])
    denied(change(clients['peer'], p.system, {'status': 'done'}), 403, 'FORBIDDEN')
    done = change(owner, p.system, {'status': 'done'})
    result = ok(done)
    assert result['status'] == 'done' and result['revision'] == 2 and result['completedAt']
    second = ok(change(pm, p.system, {'dueAt': DUE}, revision=2))
    assert second['completedAt'] == result['completedAt'] and second['revision'] == 3
    final = ok(change(pm, p.system, {'status': 'blocked'}, revision=3))
    assert final['status'] == 'blocked' and 'completedAt' not in final
    with engine.connect() as c:
        tasks = db.tasks.c
        audit, outbox, receipts = db.audit_logs.c, db.outbox_events.c, db.idempotency_keys.c
        assert c.execute(sa.select(tasks.status, tasks.completed_at, tasks.revision).where(tasks.id == UUID(p.system))).one() == ('blocked', None, 4)
        assert c.execute(sa.select(db.projects.c.stage, db.projects.c.revision).where(db.projects.c.id == p.id)).one() == ('feasibility_review', PROJECT_REVISION)
        assert c.scalar(sa.select(sa.func.count()).select_from(db.decisions).where(db.decisions.c.project_id == p.id)) == 0
        receipt = c.execute(sa.select(receipts.resource_id, receipts.operation).where(receipts.actor_id == people['pm'].account,
                            receipts.idempotency_key == h['Idempotency-Key'])).one()
        assert receipt == (UUID(task['taskId']), CREATE)
        for res, op, actor, event in [(first, CREATE, 'pm', 'task.created'), (done, UPDATE, 'owner', 'task.updated')]:
            rid = res.headers['X-Request-ID']
            actor_id, after, before = c.execute(sa.select(audit.actor_id, audit.after_hash, audit.before_hash)
                                                  .where(audit.request_id == rid, audit.action == op, audit.result == 'success')).one()
            assert actor_id == people[actor].account and after and bool(before) == (op == UPDATE)
            kind, payload = c.execute(sa.select(outbox.event_type, outbox.payload).where(outbox.dedupe_key == rid)).one()
            item = res.json()['data']
            assert kind == event and payload == {'projectId': str(p.id), 'taskId': item['taskId'],
                                                 'status': item['status'], 'revision': item['revision']}


def test_task_source_and_assignee_are_project_bound(live, team):
    engine, _, _, _ = live
    people, clients = team
    pm = clients['pm']
    p, other, foreign = project(engine, people), project(engine, people), project(engine, people, foreign=True)
    for source in p.sources:
        good = command(p, source)
        for bad in (other.sources[source], foreign.sources[source], p.id, uuid4()):
            denied(pm.post(p.path, headers=headers(PROJECT_REVISION), json={**good, 'sourceId': str(bad)}), 422, 'INVALID_TASK_SOURCE')
        unassigned = ok(pm.post(p.path, headers=headers(PROJECT_REVISION), json=good))
        assert unassigned['sourceType'] == source and 'assigneeId' not in unassigned and 'dueAt' not in unassigned
    for name in ('pending', 'foreign'):
        denied(pm.post(p.path, headers=headers(PROJECT_REVISION), json=command(p, assigneeId=str(people[name].id))),
               422, 'INVALID_TASK_ASSIGNEE')
    for fields in ({'title': '  '}, {'sourceType': 'manual'}, {'dueAt': '2027-01-16T14:00:00'}, {'dueAt': 1800000000},
                   {'status': 'done'}, {'assigneeId': None}):
        denied(pm.post(p.path, headers=headers(PROJECT_REVISION), json=command(p, **fields)), 422, 'VALIDATION_FAILED')
    for fields in ({}, {'status': None}, {'status': 'open'}, {'dueAt': None}, {'title': 'new title'}, {'stage': 'executing'}):
        denied(change(pm, p.system, fields), 422, 'VALIDATION_FAILED')
    denied(clients['foreign'].get(p.path), 404, 'RESOURCE_NOT_FOUND')
    denied(clients['pending'].get(p.path), 404, 'RESOURCE_NOT_FOUND')
    denied(change(pm, foreign.system, {'status': 'done'}), 404, 'RESOURCE_NOT_FOUND')


def test_task_storage_enforces_status_completion_and_source(live, team):
    from test_postgres_invariants import constraint

    engine, _, _, _ = live
    people, clients = team
    local, remote = project(engine, people), project(engine, people, foreign=True)
    t = db.tasks
    values = dict(project_id=local.id, source_type='review', source_id=local.sources['review'],
                  status='todo', title='Synthetic constraint probe', created_by=people['pm'].id)
    with engine.begin() as c:
        for change_to, name in (({'status': 'typo'}, 'ck_task_status'), ({'status': 'open'}, 'ck_task_status'),
                ({'status': 'done'}, 'ck_task_completion'), ({'completed_at': sa.func.now()}, 'ck_task_completion'),
                ({'status': 'done', 'completed_at': sa.text("'infinity'::timestamptz")}, 'ck_task_completion'),
                ({'source_type': 'manual'}, 'ck_task_source_scope'), ({'source_id': uuid4()}, 'ck_task_source_scope')):
            constraint(c, t.insert().values(**{**values, **change_to}), name, '23514')
        for kind in local.sources:
            constraint(c, t.insert().values(**{**values, 'source_type': kind, 'source_id': remote.sources[kind]}),
                       'ck_task_source_scope', '23514')
        task_id = c.scalar(t.insert().values(**values).returning(t.c.id))
        constraint(c, t.update().where(t.c.id == task_id).values(source_id=remote.sources['review']),
                   'ck_task_source_immutable', '23514')
        rev = db.reviews.c
        constraint(c, db.reviews.update().where(rev.id == local.sources['review']).values(project_id=remote.id),
                   'ck_task_source_in_use', '23514')
        risk_id = local.sources['remediation']
        c.execute(t.insert().values(**{**values, 'source_type': 'remediation', 'source_id': risk_id}))
        constraint(c, db.risks.delete().where(db.risks.c.id == risk_id), 'ck_task_source_in_use', '23514')

    completed = ok(change(clients['pm'], task_id, {'status': 'done'}))
    assert completed['completedAt'] and completed['sourceId'] == str(local.sources['review'])
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == local.id).values(stage='archived'))
        constraint(c, t.update().where(t.c.id == task_id).values(status='todo', completed_at=None),
                   'ck_archived_project_read_only', '23514')
    assert next(row for row in ok(clients['pm'].get(local.path))['items'] if row['taskId'] == str(task_id))['status'] == 'done'


def test_task_binding_serializes_against_source_removal(live, team):
    from test_postgres_invariants import constraint

    engine, _, _, _ = live
    people, _ = team
    p = project(engine, people)
    target = p.sources['remediation']
    waiter = Queue()

    def remove_source():
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL lock_timeout='8s'")
            waiter.put(connection.scalar(sa.text('SELECT pg_backend_pid()')))
            constraint(connection, db.risks.delete().where(db.risks.c.id == target), 'ck_task_source_in_use', '23514')

    with ThreadPoolExecutor(1) as pool:
        with engine.begin() as c:
            blocker = c.scalar(sa.text('SELECT pg_backend_pid()'))
            c.execute(db.tasks.insert().values(project_id=p.id, source_type='remediation', source_id=target,
                status='todo', title='Source remains bound', created_by=people['pm'].id))
            competing = pool.submit(remove_source)
            waiting = waiter.get(timeout=5)
            with engine.connect() as probe:
                deadline = monotonic() + 5
                query = sa.text('SELECT :blocker = ANY(pg_blocking_pids(:waiter))')
                while not probe.scalar(query, {'blocker': blocker, 'waiter': waiting}):
                    assert not competing.done() and monotonic() < deadline, 'Source deletion did not await task binding'
                    sleep(0.02)
        competing.result(timeout=5)
    with engine.connect() as c:
        assert c.scalar(sa.select(db.risks.c.id).where(db.risks.c.id == target)) == target


def test_task_revision_race_and_archived_replay(live, team):
    engine, _, _, _ = live
    people, clients = team
    p = project(engine, people)
    inputs = [('pm', headers(1), 'blocked'), ('owner', headers(1), 'in_progress')]

    def save(attempt):
        actor, h, status = attempt
        return change(clients[actor], p.system, {'status': status}, headers=h)

    with ThreadPoolExecutor(2) as pool:
        replies = list(pool.map(save, inputs))
    assert sorted(r.status_code for r in replies) == [200, 409]
    winner = next(i for i, r in enumerate(replies) if r.is_success)
    assert ok(replies[winner])['revision'] == 2
    denied(replies[1-winner], 409, 'VERSION_CONFLICT')
    assert save(inputs[winner]).json() == replies[winner].json()
    actor, h, status = inputs[winner]
    denied(change(clients[actor], p.system, {'status': 'done'}, headers=h), 409, 'IDEMPOTENCY_CONFLICT')
    pm = clients['pm']
    create_headers = headers(PROJECT_REVISION)
    created = pm.post(p.path, headers=create_headers, json=command(p))
    ok(created)
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == p.id).values(stage='archived', revision=PROJECT_REVISION+1))
    archived = ok(pm.get(p.path))
    assert not archived['canCreate'] and all(not row['canUpdate'] for row in archived['items'])
    denied(pm.post(p.path, headers=headers(PROJECT_REVISION+1), json=command(p)), 422, 'PROJECT_ARCHIVED')
    denied(change(clients[actor], p.system, {'status': status}, revision=2), 422, 'PROJECT_ARCHIVED')
    assert save(inputs[winner]).json() == replies[winner].json()
    assert pm.post(p.path, headers=create_headers, json=command(p)).json() == created.json()
    with engine.begin() as c:
        m = db.project_members.c
        c.execute(db.project_members.update().where(m.project_id == p.id, m.person_id == people[actor].id)
                  .values(assignment_status='revoked', revoked_at=sa.func.now()))
    denied(save(inputs[winner]), 404, 'RESOURCE_NOT_FOUND')


def test_task_list_paginates_the_authorized_rows_in_rest_and_stdio(live, team):
    engine, api, port, _ = live
    people, clients = team
    pm, owner = clients['pm'], clients['owner']
    p, outside = project(engine, people), project(engine, people)
    assigned = ok(pm.post(p.path, headers=headers(PROJECT_REVISION), json=command(
        p, 'rule_event', assigneeId=str(people['owner'].id), dueAt=DUE)))
    hidden = ok(pm.post(p.path, headers=headers(PROJECT_REVISION), json=command(p)))
    assert ok(pm.get(p.path))['items'][0]['taskId'] == hidden['taskId']
    token = issue(owner, [LIST], [p.id]).headers['X-PMO-New-Token']
    auth = {'Authorization': 'Bearer '+token}

    async def flow():
        params = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'pmo.mcp_server'],
            env={'PMO_API_PORT': str(port), 'PMO_AGENT_TOKEN': token, 'PYTHONUTF8': '1'})
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            args = {'projectId': str(p.id)}
            for page, expected in ((1, [assigned['taskId']]), (2, [p.system]), (3, []), (10**21, [])):
                paging = {'page': page, 'pageSize': 1}
                result = await session.call_tool(LIST, {**args, **paging})
                assert not result.isError, result
                direct = api.get('/api/v1/'+p.path, headers=auth, params=paging)
                agent = ok(direct)
                human = ok(owner.get(p.path, params=paging))
                assert agent == result.structuredContent['data'] == {
                    'items': [{k: v for k, v in item.items() if k not in ('sourceType', 'sourceId', 'completedAt', 'canUpdate')}
                              for item in human['items']], 'page': page, 'pageSize': 1, 'total': 2,
                }
                assert [item['taskId'] for item in agent['items']] == expected
                refs = [f"task:{item['taskId']}@{item['revision']}" for item in agent['items']]
                assert result.structuredContent['meta']['sourceRefs'] == direct.json()['meta']['sourceRefs'] == refs
            for invalid in ({'page': 0}, {'pageSize': 0}, {'pageSize': 101}):
                result = await session.call_tool(LIST, {**args, **invalid})
                assert result.isError and result.structuredContent['error']['code'] == 'VALIDATION_FAILED'
                denied(api.get('/api/v1/'+p.path, headers=auth, params=invalid), 422, 'VALIDATION_FAILED')
            result = await session.call_tool(LIST, {'projectId': str(outside.id), 'page': 2})
            assert result.isError and result.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND'

    asyncio.run(flow())


def test_live_task_token_issuance_and_closed_mcp_dto(live, team):
    engine, api, port, _ = live
    people, clients = team
    pm, owner = clients['pm'], clients['owner']
    p, other, archived = project(engine, people), project(engine, people), project(engine, people, stage='archived')
    denied(issue(clients['peer'], [UPDATE], [p.id]), 403, 'FORBIDDEN')
    denied(issue(pm, [UPDATE], [archived.id]), 403, 'FORBIDDEN')
    denied(issue(pm, [CREATE], [p.id]), 403, 'INVALID_GRANT')
    assert issue(pm, [LIST, UPDATE, 'pmo_project_create'], [], allow_create=True).status_code in (403, 422)
    pm_grant = issue(pm, [UPDATE], [p.id])
    ok(pm_grant)
    assert 'X-PMO-New-Token' in pm_grant.headers
    read_only = issue(pm, [LIST], [p.id])
    ok(read_only)
    read_token = read_only.headers['X-PMO-New-Token']
    denied(api.patch('/api/v1/tasks/'+p.system, headers={**headers(1), 'Authorization': 'Bearer '+read_token},
                     json={'fieldsTask': {'status': 'done'}}), 403, 'FORBIDDEN')
    grant = issue(owner, [LIST, UPDATE], [p.id])
    credential = UUID(ok(grant)['tokenId'])
    token = grant.headers['X-PMO-New-Token']
    auth = {'Authorization': 'Bearer '+token}
    denied(api.post('/api/v1/'+p.path, headers={**headers(PROJECT_REVISION), **auth}, json=command(p)), 403, 'FORBIDDEN')
    # Another writable task in the allowed project is not assigned to the delegator.
    unassigned = ok(pm.post(p.path, headers=headers(PROJECT_REVISION), json=command(p)))
    with engine.connect() as c:
        assert c.scalar(sa.select(explicit_grants.c.granted_by).where(explicit_grants.c.credential_id == credential,
                        explicit_grants.c.project_id == p.id)) == people['owner'].account

    async def flow():
        params = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'pmo.mcp_server'],
            env={'PMO_API_PORT': str(port), 'PMO_AGENT_TOKEN': token, 'PYTHONUTF8': '1'})
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {LIST, UPDATE} <= names and CREATE not in names
            result = await session.call_tool(LIST, {'projectId': str(p.id)})
            assert not result.isError, result
            listed = result.structuredContent['data']
            assert set(listed) == {'items', 'page', 'pageSize', 'total'} and listed['total'] == 1
            [task] = listed['items']
            assert task['taskId'] == p.system and task['status'] == 'todo'
            assert set(task) == {'taskId', 'projectId', 'title', 'status', 'revision', 'assigneeId'}
            body = {'fieldsTask': {'status': 'done'}, 'expectedRevision': 1, 'idempotencyKey': uuid4().hex}
            for task_id, code in ((other.system, 'RESOURCE_NOT_FOUND'), (unassigned['taskId'], 'FORBIDDEN')):
                refused = await session.call_tool(UPDATE, {**body, 'taskId': task_id})
                assert refused.isError and refused.structuredContent['error']['code'] == code
            args = {**body, 'taskId': p.system}
            saved = await session.call_tool(UPDATE, args)
            assert not saved.isError, saved
            assert saved.structuredContent['data'] == {**task, 'status': 'done', 'revision': 2}
            assert (await session.call_tool(UPDATE, args)).structuredContent == saved.structuredContent
            with engine.begin() as c:
                c.execute(db.tasks.update().where(db.tasks.c.id == UUID(p.system)).values(assignee_id=people['peer'].id))
            refused = await session.call_tool(UPDATE, args)
            assert refused.isError and refused.structuredContent['error']['code'] == 'FORBIDDEN'
            with engine.begin() as c:
                c.execute(explicit_grants.delete().where(explicit_grants.c.credential_id == credential))
            refused = await session.call_tool(LIST, {'projectId': str(p.id)})
            assert refused.isError and refused.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND'

    asyncio.run(flow())
    with engine.connect() as c:
        l = db.audit_logs.c
        assert c.execute(sa.select(l.actor_type, l.delegated_by).where(l.actor_id == credential, l.action == UPDATE,
                         l.result == 'success')).one() == ('agent', people['owner'].account)


def test_task_outbox_failure_rolls_back_write_and_receipt(live, team):
    engine, _, _, _ = live
    people, clients = team
    p = project(engine, people)
    pm = clients['pm']
    create_h, patch_h = headers(PROJECT_REVISION), headers(1)
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER task_test_failure BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    try:
        denied(pm.post(p.path, headers=create_h, json=command(p)), 500, 'DATABASE_FAILURE')
        denied(change(pm, p.system, {'status': 'done'}, headers=patch_h), 500, 'DATABASE_FAILURE')
        with engine.connect() as c:
            assert c.scalar(sa.select(sa.func.count()).select_from(db.tasks).where(db.tasks.c.project_id == p.id)) == 1
            assert c.execute(sa.select(db.tasks.c.status, db.tasks.c.completed_at, db.tasks.c.revision)
                         .where(db.tasks.c.id == UUID(p.system))).one() == ('todo', None, 1)
            for log in (db.idempotency_keys, db.audit_logs):
                assert c.scalar(sa.select(sa.func.count()).select_from(log).where(log.c.idempotency_key.in_(
                    [create_h['Idempotency-Key'], patch_h['Idempotency-Key']]))) == 0
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER task_test_failure ON outbox_events')
    assert ok(pm.post(p.path, headers=create_h, json=command(p)))['revision'] == 1
    assert ok(change(pm, p.system, {'status': 'done'}, headers=patch_h))['revision'] == 2
