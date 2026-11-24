import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
import sys
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
from pmo.identity import Actor, Failure
from pmo.projects import fingerprint
from pmo.retrospectives import CONFIRM, DRAFT, SAVE, SEARCH, archive_basis

ANSWERS = ['Actual cost reached the approved budget; scope stayed fixed.',
           'A supplier delay affected delivery, not the requirement baseline.',
           'Reuse the signed delivery checklist.',
           'Validate dependencies at charter and price them in the plan.']
SPEC_QUESTIONS = [
    '目标对照：立项卡承诺的收益 / 成本 / 周期，实际各是多少？差异主要原因？',
    '过程表现：三线偏差各发生在哪个阶段？是估算问题、执行问题还是范围问题？',
    '可复用资产：哪些做法 / 模板 / 方案值得沉淀，下次直接用？',
    '教训与建议：如果重来一次，立项和方案阶段分别会做什么不同的事？（写入 Lesson，带分类标签）',
]
LESSON = {'category': 'technical', 'what': 'An early prototype exposed the delivery dependency.',
          'rootCause': 'The dependency was not exercised in the design review.',
          'advice': 'Add a dependency exercise to the plan.'}


def headers(revision=None):
    result = {'Idempotency-Key': uuid4().hex}
    if revision is not None:
        result['If-Match'] = str(revision)
    return result


def data(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def error(response, status, code=None):
    assert response.status_code == status, response.text
    if code is not None:
        assert response.json()['code'] == code


def _person(c, org):
    person, username, password = uuid4(), uuid4().hex, token_urlsafe(24)
    account = c.scalar(db.user_accounts.insert().values(username=username, is_active=True,
        password_hash=PasswordHasher(time_cost=1, memory_cost=8192).hash(password)).returning(db.user_accounts.c.id))
    c.execute(db.persons.insert().values(id=person, user_id=account, created_by=person, org_id=org,
                                        name='Synthetic retrospective user', is_active=True))
    return SimpleNamespace(id=person, org=org, account=account, username=username, password=password)


@pytest.fixture
def team(live):
    engine, _, port, _ = live
    with engine.begin() as c:
        def create_org():
            return c.scalar(db.organizations.insert().values(name='Retrospective test', status='active')
                            .returning(db.organizations.c.id))
        local, foreign = create_org(), create_org()
        people = {name: _person(c, org) for name, org in (
            ('pm', local), ('reviewer', local), ('foreign_pm', foreign), ('foreign_reviewer', foreign))}
        c.execute(db.user_roles.insert().values(user_id=people['pm'].account, role='delegator'))
    with ExitStack() as stack:
        sessions = {}
        for name, p in people.items():
            client = stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/', trust_env=False))
            login = client.post('auth/login', json={'username': p.username, 'password': p.password})
            assert login.status_code == 200, login.text
            client.headers['X-CSRF-Token'] = login.json()['csrfToken']
            sessions[name] = client
        yield people, sessions


def project(engine, team, *, origin='closed', foreign=False):
    people, _ = team
    pm, reviewer = (people['foreign_pm'], people['foreign_reviewer']) if foreign else (people['pm'], people['reviewer'])
    # Seed predecessor evidence only in the isolated schema: lifecycle entry/approval is owned by another module.
    with engine.begin() as c:
        pid = c.scalar(db.projects.insert().values(org_id=pm.org, code=uuid4().hex, name='Retrospective fixture',
            initiator_id=pm.id, created_by=pm.id, pm_id=pm.id, revision=2, stage='retrospective').returning(db.projects.c.id))
        for person, role in ((pm, 'pm'), (pm, 'reviewer'), (reviewer, 'reviewer'), (reviewer, 'observer')):
            c.execute(db.project_members.insert().values(project_id=pid, person_id=person.id, role=role,
                assignment_status='accepted', accepted_at=sa.func.now(), assigned_by=pm.id))
        if origin:
            approval_id = uuid4()
            why = 'Synthetic completed transition into retrospective'
            c.execute(db.approval_requests.insert().values(id=approval_id, project_id=pid, action='project_stage_transition',
                expected_revision=1, status='applied', requested_by=pm.id, decided_by=pm.id, decided_at=sa.func.now(),
                rule_version=RULE_VERSION, reason=why, snapshot_hash=fingerprint({}),
                payload_hash=fingerprint([str(pid), origin, 'retrospective', why])))
            c.execute(sa.text('''INSERT INTO project_transition_intents
                (approval_id, from_stage, to_stage, actor_type, reason, basis, source_refs)
                VALUES (:approval_id, :origin, 'retrospective', 'human', :reason, '{}', '[]')'''),
                {'approval_id': approval_id, 'origin': origin, 'reason': why})
            c.execute(sa.text('''INSERT INTO project_stage_transitions
                (project_id, from_revision, to_revision, from_stage, to_stage, actor_id, actor_type, request_id, approval_request_id)
                VALUES (:project_id, 1, 2, :origin, 'retrospective', :actor_id, 'human', :request_id, :approval_id)'''),
                {'project_id': pid, 'origin': origin, 'actor_id': pm.id, 'request_id': uuid4(), 'approval_id': approval_id})
    return pid


def basis(engine, pid, user):
    with engine.begin() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id == pid).with_for_update()).mappings().one()
        actor = Actor(user.account, user.id, user.account, user.org, frozenset(), (), False, 'human')
        return archive_basis(c, actor, p)


def test_retrospective_and_independent_lesson_then_archived_replay(live, team):
    engine, api, _, _ = live
    people, sessions = team
    pm, reviewer, other = (sessions[name] for name in ('pm', 'reviewer', 'foreign_pm'))
    pid = project(engine, team)
    retro = f'projects/{pid}/retrospective'
    lessons = f'projects/{pid}/lessons'
    assert data(pm.get(retro)) == {'projectId': str(pid), 'stage': 'retrospective', 'kind': 'normal',
        'fromStage': 'closed', 'retrospectiveId': None, 'revision': 1, 'answers': [], 'completedAt': None, 'canSave': True,
        'questions': SPEC_QUESTIONS, 'costDiscussion': []}
    assert not data(reviewer.get(retro))['canSave']
    error(other.get(retro), 404)
    error(other.get(lessons), 404)
    error(api.get(f'/api/v1/{retro}'), 401)
    with pytest.raises(Failure) as exc:
        basis(engine, pid, people['reviewer'])
    assert (exc.value.status, exc.value.code) == (422, 'RETROSPECTIVE_ANSWERS_REQUIRED')

    body = {'answers': ANSWERS}
    for invalid in ({'answers': ANSWERS[:3]}, {'answers': [*ANSWERS[:3], '    ']},
                    {'answers': [*ANSWERS[:3], ' TBD. ']}, {**body, 'kind': 'normal'}):
        error(pm.put(retro, headers=headers(1), json=invalid), 422, 'VALIDATION_FAILED')
    error(pm.put(retro, headers=headers(), json=body), 422)
    error(reviewer.put(retro, headers=headers(1), json=body), 403, 'PM_ASSIGNMENT_REQUIRED')
    save_key = headers(1)
    saved = pm.put(retro, headers=save_key, json=body)
    assert data(saved)['revision'] == 2 and data(saved)['completedAt']
    assert 'questions' not in data(saved)  # Keep the stored write receipt stable across updates.
    assert pm.put(retro, headers=save_key, json=body).json() == saved.json()
    error(pm.put(retro, headers=save_key, json={'answers': ANSWERS[::-1]}), 409, 'IDEMPOTENCY_CONFLICT')
    error(pm.put(retro, headers=headers(1), json=body), 409, 'VERSION_CONFLICT')
    revised_answers = [*ANSWERS[:3], 'Validate dependencies at charter and record their cost in the approved plan.']
    revised = data(pm.put(retro, headers=headers(2), json={'answers': revised_answers}))
    assert revised['revision'] == 3 and revised['retrospectiveId'] == data(saved)['retrospectiveId']
    assert data(pm.get(retro))['answers'] == revised_answers

    drafts = headers()
    error(reviewer.post(lessons, headers=drafts, json=LESSON), 403, 'PM_ASSIGNMENT_REQUIRED')
    draft = pm.post(lessons, headers=drafts, json=LESSON)
    lesson = data(draft)
    assert lesson['status'] == 'draft' and lesson['revision'] == 1 and lesson['confirmedBy'] is None
    assert pm.post(lessons, headers=drafts, json=LESSON).json() == draft.json()
    assert data(reviewer.get(lessons))['items'][0]['canConfirm']
    assert not data(pm.get(lessons))['items'][0]['canConfirm']
    with pytest.raises(Failure) as exc:
        basis(engine, pid, people['reviewer'])
    assert (exc.value.status, exc.value.code) == (422, 'CONFIRMED_LESSON_REQUIRED')

    path = f"lessons/{lesson['lessonId']}/confirm"
    error(other.post(path, headers=headers(1), json={}), 404)
    error(pm.post(path, headers=headers(1), json={}), 403, 'INDEPENDENT_REVIEWER_REQUIRED')
    error(reviewer.post(path, headers=headers(1), json={'decision': 'approve'}), 422, 'VALIDATION_FAILED')
    error(reviewer.post(path, headers=headers(2), json={}), 409, 'VERSION_CONFLICT')
    confirm_key = headers(1)
    signed = reviewer.post(path, headers=confirm_key)
    assert data(signed)['revision'] == 2 and data(signed)['status'] == 'confirmed'
    assert data(signed)['confirmedBy'] == str(people['reviewer'].id) and data(signed)['confirmedAt']
    assert reviewer.post(path, headers=confirm_key, json={}).json() == signed.json()
    error(reviewer.post(path, headers=headers(2)), 409, 'LESSON_NOT_DRAFT')
    archive, refs = basis(engine, pid, people['reviewer'])
    assert archive['kind'] == 'normal' and archive['entryRevision'] == 2
    assert archive['retrospective']['answers'] == revised_answers and archive['lessons'][0]['lessonId'] == lesson['lessonId']
    assert refs == [f'project-transition:{pid}@2', f"retrospective:{revised['retrospectiveId']}@3",
                    f"lesson:{lesson['lessonId']}@2"]

    with engine.begin() as c:
        assert c.scalar(sa.select(db.projects.c.stage).where(db.projects.c.id == pid)) == 'retrospective'
        assert not c.scalar(sa.select(db.decisions.c.id).where(db.decisions.c.project_id == pid))
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='archived', revision=3))
    archived = data(pm.get(retro))
    assert archived['stage'] == 'archived' and archived['fromStage'] == 'closed' and not archived['canSave']
    assert archived['questions'] == SPEC_QUESTIONS and archived['answers'] == revised_answers
    assert pm.put(retro, headers=save_key, json=body).json() == saved.json()
    assert pm.post(lessons, headers=drafts, json=LESSON).json() == draft.json()
    assert reviewer.post(path, headers=confirm_key).json() == signed.json()
    error(pm.put(retro, headers=headers(3), json=body), 422, 'PROJECT_ARCHIVED')
    error(pm.post(lessons, headers=headers(), json=LESSON), 422, 'PROJECT_ARCHIVED')
    error(reviewer.post(path, headers=headers(2)), 422, 'PROJECT_ARCHIVED')
    with engine.begin() as c:
        member = db.project_members
        c.execute(member.update().where(member.c.project_id == pid, member.c.role.in_(('pm', 'reviewer')))
                  .values(revoked_at=sa.func.now(), assignment_status='revoked'))
        a = db.audit_logs
        audited = c.execute(sa.select(a.c.action).where(a.c.project_id == pid, a.c.result == 'success')).scalars().all()
        o = db.outbox_events
        events = c.execute(sa.select(o.c.event_type).where(o.c.payload['projectId'].astext == str(pid))).scalars().all()
    assert sorted(audited) == sorted([SAVE, SAVE, DRAFT, CONFIRM])
    assert sorted(events) == ['lesson.confirmed', 'lesson.drafted', 'retrospective.saved', 'retrospective.saved']
    error(pm.put(retro, headers=save_key, json=body), 404)
    error(reviewer.post(path, headers=confirm_key), 403, 'INDEPENDENT_REVIEWER_REQUIRED')


def test_termination_origin_and_revision_race(live, team):
    engine = live[0]
    people, sessions = team
    pm, reviewer = sessions['pm'], sessions['reviewer']
    missing = project(engine, team, origin=None)
    no_origin = data(pm.get(f'projects/{missing}/retrospective'))
    assert no_origin['kind'] is None and no_origin['fromStage'] is None and not no_origin['canSave']
    error(pm.put(f'projects/{missing}/retrospective', headers=headers(1), json={'answers': ANSWERS}),
          422, 'RETROSPECTIVE_ORIGIN_REQUIRED')
    with pytest.raises(Failure) as exc:
        basis(engine, missing, people['reviewer'])
    assert (exc.value.status, exc.value.code) == (422, 'RETROSPECTIVE_ORIGIN_REQUIRED')
    pid = project(engine, team, origin='terminated')
    retro = f'projects/{pid}/retrospective'
    view = data(pm.get(retro))
    assert view['fromStage'] == 'terminated' and view['questions'] == SPEC_QUESTIONS
    closing = data(pm.get(f'projects/{pid}/assistant/closing'))
    assert closing['retrospectiveKind'] == 'terminated' and closing['retrospectiveQuestions'] == view['questions']

    def save(i):
        return pm.put(retro, headers=headers(1), json={'answers': [f'Termination cause {i}: dependency lost.', *ANSWERS[1:]]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = sorted(pool.map(save, (1, 2)), key=lambda r: r.status_code)
    assert data(responses[0])['kind'] == 'terminated' and data(responses[0])['revision'] == 2
    error(responses[1], 409, 'VERSION_CONFLICT')
    lesson = data(pm.post(f'projects/{pid}/lessons', headers=headers(), json=LESSON))
    data(reviewer.post(f"lessons/{lesson['lessonId']}/confirm", headers=headers(1), json={}))
    archive, _ = basis(engine, pid, people['reviewer'])
    assert (archive['kind'], archive['fromStage']) == ('terminated', 'terminated')
    assert archive['retrospective']['answers'] == data(responses[0])['answers']


def test_closed_search_dto_and_live_delegation_scope(live, team):
    engine, api, port, _ = live
    people, sessions = team
    pm = sessions['pm']
    visible = project(engine, team)
    excluded = project(engine, team)
    foreign = project(engine, team, foreign=True)
    for pid, author, reviewer in ((visible, 'pm', 'reviewer'), (excluded, 'pm', 'reviewer'),
                                  (foreign, 'foreign_pm', 'foreign_reviewer')):
        draft = data(sessions[author].post(f'projects/{pid}/lessons', headers=headers(), json=LESSON))
        confirmed = data(sessions[reviewer].post(f"lessons/{draft['lessonId']}/confirm", headers=headers(1)))
        if pid == visible:
            expected = confirmed
    pending = data(pm.post(f'projects/{visible}/lessons', headers=headers(), json={**LESSON, 'what': 'Unconfirmed secret'}))
    assert {i['projectId'] for i in data(pm.get('lessons/search'))['items']} == {str(visible), str(excluded)}
    revision = data(pm.get('me/agent-tokens'))['collectionRevision']
    issued = pm.post('me/agent-tokens', headers=headers(revision), json={'allowedActions': [SEARCH],
        'allowedProjectIds': [str(visible)], 'expiresAt': (datetime.now(UTC) + timedelta(minutes=15)).isoformat()})
    data(issued)
    token = issued.headers['X-PMO-New-Token']
    auth = {'Authorization': 'Bearer ' + token}
    query = {'keyword': 'PROTOTYPE', 'category': 'technical', 'page': 1, 'pageSize': 1}
    result = data(api.get('/api/v1/lessons/search', headers=auth, params=query))
    assert result['total'] == 1 and result['items'][0]['lessonId'] == expected['lessonId']
    assert result['items'][0]['sourceRefs'] == [f"lesson:{expected['lessonId']}@2", f'project:{visible}@2']
    assert set(result['items'][0]) == {'lessonId', 'projectId', 'category', 'what', 'rootCause', 'advice', 'sourceRefs', 'confirmedAt'}
    for filters in ({'keyword': '%'}, {'keyword': 'Unconfirmed secret'}, {'category': 'customer'}, {'page': 2, 'pageSize': 1}):
        assert data(api.get('/api/v1/lessons/search', headers=auth, params=filters))['items'] == []
    error(api.get('/api/v1/lessons/search', headers=auth, params={'pageSize': 101}), 422)
    error(api.get(f'/api/v1/projects/{visible}/retrospective', headers=auth), 403)
    error(api.get(f'/api/v1/projects/{visible}/lessons', headers=auth), 403)
    error(api.post(f"/api/v1/lessons/{pending['lessonId']}/confirm", headers={**auth, **headers(1)}), 403)

    async def exercise():
        cfg = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'pmo.mcp_server'],
                                    env={'PMO_API_PORT': str(port), 'PMO_AGENT_TOKEN': token})
        async with stdio_client(cfg) as streams, ClientSession(*streams) as session:
            await session.initialize()
            answer = await session.call_tool(SEARCH, query)
            assert not answer.isError, answer.content
            assert answer.structuredContent['data']['items'] == result['items']
    asyncio.run(exercise())

    with engine.begin() as c:
        m = db.project_members
        c.execute(m.update().where(m.c.project_id == visible, m.c.person_id == people['pm'].id)
                  .values(assignment_status='revoked', revoked_at=sa.func.now()))
    gone = data(api.get('/api/v1/lessons/search', headers=auth))
    assert gone['total'] == 0 and gone['items'] == []


def test_confirmation_rolls_back_when_outbox_fails(live, team):
    engine = live[0]
    _, users = team
    pid = project(engine, team)
    lesson = data(users['pm'].post(f'projects/{pid}/lessons', headers=headers(), json=LESSON))
    path = f"lessons/{lesson['lessonId']}/confirm"
    key = headers(1)
    with engine.begin() as c:
        c.exec_driver_sql('''CREATE FUNCTION test_reject_lesson_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.event_type = 'lesson.confirmed' THEN RAISE EXCEPTION 'synthetic outbox failure'; END IF;
            RETURN NEW; END $$''')
        c.exec_driver_sql('''CREATE TRIGGER test_reject_lesson_outbox BEFORE INSERT ON outbox_events
            FOR EACH ROW EXECUTE FUNCTION test_reject_lesson_outbox()''')
    try:
        error(users['reviewer'].post(path, headers=key), 500, 'DATABASE_FAILURE')
        unchanged = data(users['pm'].get(f'projects/{pid}/lessons'))['items'][0]
        assert unchanged['status'] == 'draft' and unchanged['confirmedBy'] is None and unchanged['revision'] == 1
        with engine.begin() as c:
            idem, audit = db.idempotency_keys, db.audit_logs
            assert not c.scalar(sa.select(idem.c.id).where(idem.c.operation == CONFIRM,
                idem.c.resource_id == UUID(lesson['lessonId'])))
            assert not c.scalar(sa.select(audit.c.id).where(audit.c.action == CONFIRM,
                audit.c.project_id == pid, audit.c.result == 'success'))
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER test_reject_lesson_outbox ON outbox_events')
            c.exec_driver_sql('DROP FUNCTION test_reject_lesson_outbox()')
    assert data(users['reviewer'].post(path, headers=key))['status'] == 'confirmed'
