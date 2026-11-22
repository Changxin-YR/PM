import asyncio
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from secrets import token_urlsafe
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import jsonschema
import pytest
import sqlalchemy as sa
from argon2 import PasswordHasher
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy.exc import DBAPIError
from pydantic import ValidationError

from pmo import schema_v1 as db
from pmo.identity import explicit_grants
from pmo.reports import CONFIRM, GET, LIST, SUBMIT, Submit, period_bounds, reports, weekly_submissions


WEEK = '2025-W01'
START, END = date(2024, 12, 30), date(2025, 1, 6)


def sections(**changes):
    return {'milestone_progress': [], 'effort_person_days': '2.50', 'requirement_progress': [],
            'risks_and_blockers': [], **changes}


def body(**changes):
    return {'reportType': 'weekly', 'period': WEEK, 'structuredFields': sections(), **changes}


def headers(revision=None):
    result = {'Idempotency-Key': uuid4().hex}
    if revision is not None:
        result['If-Match'] = str(revision)
    return result


def data(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def deny(response, status, code):
    assert (response.status_code, response.json()['code']) == (status, code), response.text


@pytest.fixture
def team(live):
    engine, _, port, _ = live
    password = token_urlsafe(24)
    digest = PasswordHasher(time_cost=1, memory_cost=8192).hash(password)
    people = {}
    with engine.begin() as c:
        org = c.scalar(db.organizations.insert().values(name='Report test', status='active').returning(db.organizations.c.id))
        for role in ('pm', 'reporter', 'reviewer', 'outsider'):
            person, username = uuid4(), uuid4().hex
            account = c.scalar(db.user_accounts.insert().values(username=username, password_hash=digest, is_active=True)
                               .returning(db.user_accounts.c.id))
            c.execute(db.persons.insert().values(id=person, org_id=org, user_id=account, name=role, created_by=person, is_active=True))
            people[role] = SimpleNamespace(id=person, account=account, username=username, org=org)
        pm = people['pm'].id

        def project(roles):
            pid = c.scalar(db.projects.insert().values(org_id=org, code=uuid4().hex, name='Report test project',
                created_by=pm, initiator_id=pm, pm_id=pm, stage='executing', revision=1).returning(db.projects.c.id))
            for who, role in roles:
                c.execute(db.project_members.insert().values(project_id=pid, person_id=people[who].id, role=role,
                    assigned_by=pm, assignment_status='accepted', accepted_at=sa.func.now()))
            return pid

        pid = project((('pm', 'pm'), ('reporter', 'member'), ('reviewer', 'reviewer')))
        other = project((('pm', 'pm'), ('reporter', 'member'), ('outsider', 'reviewer')))
        milestones = [c.scalar(db.milestones.insert().values(project_id=project_id, created_by=pm, is_key=True,
            ordinal=1, name='Approval demo', planned_date=START).returning(db.milestones.c.id)) for project_id in (pid, other)]
    url = f'http://127.0.0.1:{port}/api/v1/'
    with ExitStack() as stack:
        users = {}
        for role, person in people.items():
            client = stack.enter_context(httpx.Client(base_url=url, trust_env=False))
            response = client.post('auth/login', json={'username': person.username, 'password': password})
            assert response.status_code == 200, response.text
            client.headers['X-CSRF-Token'] = response.json()['csrfToken']
            users[role] = client
        yield SimpleNamespace(engine=engine, pid=pid, other=other, milestones=milestones, people=people,
                              users=users, path=f'projects/{pid}/reports', url=url)


def receipt(team, *, as_of):
    with team.engine.connect() as c:
        return weekly_submissions(c, team.pid, as_of=as_of, start=START, end=END)


def test_frozen_fields_and_iso_period():
    assert period_bounds('weekly', WEEK) == (START, END)
    assert period_bounds('daily', '2024-02-29') == (date(2024, 2, 29), date(2024, 3, 1))
    invalid = [body(period='2025-W53'), body(period='2025-W1'), body(reportType='daily'), body(authorId=str(uuid4())),
               body(structuredFields=sections(effort_person_days=2.5)), body(structuredFields=sections(actual='9')),
               body(structuredFields=sections(effort_person_days='1e2')), body(structuredFields=sections(effort_person_days='1.00\n')),
               body(structuredFields={'milestone_progress': [], 'requirement_progress': [], 'risks_and_blockers': []}),
               body(structuredFields=sections(milestone_progress=[{'claim': 'completion'}]))]
    for command in invalid:
        with pytest.raises(ValidationError):
            Submit.model_validate(command)
    assert Submit.model_validate(body()).structuredFields.effort_person_days == '2.50'


def test_report_entries_close_new_writes_in_rest_and_stdio(live, team):
    from pmo.mcp_server import tools

    pm = team.users['pm']
    malformed = [sections(milestone_progress=[{'milestoneId': str(team.milestones[0]), 'verified': True}]),
                 sections(requirement_progress=[{'description': {'unexpected': 'object'}}]),
                 sections(risks_and_blockers=[{'milestoneId': str(team.milestones[0]), 'summary': 'Wrong kind'}]),
                 sections(milestone_progress=[{'claim': 'completion'}]),
                 sections(effort_person_days=2.5), sections(effort_person_days='2.50\n')]
    for fields in malformed:
        deny(pm.post(team.path, headers=headers(), json=body(structuredFields=fields)), 422, 'VALIDATION_FAILED')
    deny(pm.post(team.path + '/drafts', headers=headers(), json=body(structuredFields=malformed[0])), 422, 'VALIDATION_FAILED')
    draft = data(pm.post(team.path + '/drafts', headers=headers(), json=body()))
    deny(pm.patch(f"reports/{draft['reportId']}", headers=headers(1),
                  json={'structuredFields': malformed[2]}), 422, 'VALIDATION_FAILED')

    _, _, port, _ = live
    token = _token(team)
    config = StdioServerParameters(command=sys.executable, args=['-m', 'pmo.mcp_server'],
        env={'PMO_API_PORT': str(port), 'PMO_AGENT_TOKEN': token['Authorization'].split()[1], 'PYTHONUTF8': '1'})

    async def calls():
        async with stdio_client(config) as streams, ClientSession(*streams) as session:
            await session.initialize()
            schema = next(t.inputSchema for t in (await session.list_tools()).tools if t.name == SUBMIT)
            for fields in malformed:
                args = {**body(structuredFields=fields), 'projectId': str(team.pid), 'idempotencyKey': uuid4().hex}
                assert not jsonschema.Draft202012Validator(schema).is_valid(args)
                denied = await session.call_tool(SUBMIT, args)
                assert denied.isError and denied.structuredContent['error']['code'] == 'VALIDATION_FAILED'
            valid = sections(milestone_progress=[{'milestoneId': str(team.milestones[0]), 'claim': 'progress',
                'summary': 'At 2.50 days'}], requirement_progress=[{'description': 'A requirement note'}],
                risks_and_blockers=[{'name': 'A risk note'}])
            args = {**body(structuredFields=valid), 'projectId': str(team.pid), 'idempotencyKey': uuid4().hex}
            jsonschema.Draft202012Validator(schema).validate(args)
            posted = await session.call_tool(SUBMIT, args)
            assert not posted.isError and posted.structuredContent['data']['structuredFields'] == valid
            repeated = await session.call_tool(SUBMIT, args)
            assert not repeated.isError and repeated.structuredContent == posted.structuredContent
            report_id = posted.structuredContent['data']['reportId']
            found = await session.call_tool(GET, {'reportId': report_id})
            assert not found.isError and found.structuredContent['data']['structuredFields'] == valid
            result = found.structuredContent
            result['data']['structuredFields']['risks_and_blockers'][0]['arbitrary'] = True
            assert not jsonschema.Draft202012Validator(tools[GET].outputSchema).is_valid(result)
    asyncio.run(calls())
    with team.engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(reports).where(reports.c.project_id == team.pid)) == 2


def test_draft_submit_publish_and_history(team):
    pm, author, reviewer = (team.users[k] for k in ('pm', 'reporter', 'reviewer'))
    draft_request = body(structuredFields={}, narrative='Private draft note')
    key = headers()
    reply = author.post(team.path + '/drafts', headers=key, json=draft_request)
    draft = data(reply)
    assert author.post(team.path + '/drafts', headers=key, json=draft_request).json() == reply.json()
    assert draft['status'] == 'draft' and draft['versionNo'] == 1 and draft['canEdit']
    path = f"reports/{draft['reportId']}"
    deny(reviewer.get(path), 404, 'RESOURCE_NOT_FOUND')
    assert data(reviewer.get(team.path))['total'] == 0
    assert data(pm.get(path))['canSubmit']
    assert receipt(team, as_of=datetime.now(UTC)) == []
    deny(pm.post(path + '/confirm', headers=headers(1), json={}), 422, 'REPORT_NOT_SUBMITTED')
    deny(author.post(path + '/submit', headers=headers(1), json={}), 422, 'REPORT_INCOMPLETE')

    edit = {'structuredFields': sections(), 'narrative': 'Fixed human report'}
    deny(pm.patch(path, headers=headers(1), json=edit), 403, 'FORBIDDEN')
    updated = data(author.patch(path, headers=headers(1), json=edit))
    assert updated['revision'] == 2 and updated['contentHash'] != draft['contentHash']
    deny(author.patch(path, headers=headers(1), json=edit), 409, 'VERSION_CONFLICT')
    submit_key = headers(2)
    saved = pm.post(path + '/submit', headers=submit_key, json={'reason': 'Reviewed original report'})
    submitted = data(saved)
    assert submitted['status'] == 'submitted' and submitted['revision'] == 3
    assert submitted['authorId'] == str(team.people['reporter'].id) and submitted['submittedBy'] == str(team.people['pm'].id)
    deny(author.patch(path, headers=headers(3), json=edit), 422, 'REPORT_NOT_DRAFT')
    deny(author.post(path + '/confirm', headers=headers(3), json={}), 403, 'FORBIDDEN')
    confirm_key = headers(3)
    published = pm.post(path + '/confirm', headers=confirm_key)
    assert data(published)['isCurrentConfirmed'] and data(published)['confirmedBy'] == str(team.people['pm'].id)
    assert data(published)['revision'] == 4
    assert pm.post(path + '/confirm', headers=confirm_key, json={}).json() == published.json()
    assert pm.post(path + '/submit', headers=submit_key, json={'reason': 'Reviewed original report'}).json() == saved.json()
    deny(pm.post(path + '/submit', headers=submit_key, json={'reason': 'Changed request'}), 409, 'IDEMPOTENCY_CONFLICT')

    submit_time = datetime.fromisoformat(submitted['submittedAt'])
    assert receipt(team, as_of=submit_time - timedelta(microseconds=1)) == []
    assert receipt(team, as_of=submit_time)[0].revision == 3
    replacement = data(author.post(team.path + '/drafts', headers=headers(), json=body()))
    assert not data(pm.get(path))['isCurrent'] and data(pm.get(path))['isCurrentConfirmed']
    second = f"reports/{replacement['reportId']}"
    data(author.post(second + '/submit', headers=headers(1), json={}))
    newer = data(pm.post(team.path, headers=headers(), json=body(narrative='Newer report')))
    latest = data(pm.post(f"reports/{newer['reportId']}/confirm", headers=headers(1), json={}))
    deny(pm.post(second + '/confirm', headers=headers(2), json={}), 409, 'REPORT_VERSION_SUPERSEDED')
    prior = data(pm.get(path))
    assert not prior['isCurrentConfirmed'] and prior['narrative'] == edit['narrative'] and prior['revision'] == 4
    first_confirmed = datetime.fromisoformat(prior['confirmedAt'])
    last_confirmed = datetime.fromisoformat(latest['confirmedAt'])
    for cutoff, expected in ((first_confirmed-timedelta(microseconds=1), None),
                             (first_confirmed, prior), (last_confirmed-timedelta(microseconds=1), prior),
                             (last_confirmed, latest)):
        active = [submission for submission in receipt(team, as_of=cutoff) if submission.confirmed_at is not None]
        if expected is None:
            assert not active
        else:
            [publication] = active
            assert str(publication.report_id) == expected['reportId']
            assert publication.confirmed_revision == expected['revision']
            assert publication.confirmed_at == datetime.fromisoformat(expected['confirmedAt'])
    page = data(pm.get(team.path, params={'pageSize': 2}))
    assert page['total'] == 3 and [i['versionNo'] for i in page['items']] == [3, 2]


def _token(team, *, person='reporter', actions=(LIST, GET, SUBMIT), explicit=True):
    owner = team.people[person]
    secret = token_urlsafe(32)
    digest = hashlib.sha256(secret.encode()).hexdigest()
    with team.engine.begin() as c:
        credential = c.scalar(db.agent_credentials.insert().values(delegated_by=owner.account, org_id=owner.org,
            allowed_actions=list(actions), allowed_project_ids=[str(team.pid)], allow_project_create=False, status='active',
            expires_at=datetime.now(UTC) + timedelta(minutes=10), token_hash=digest, token_fingerprint=digest[:12])
            .returning(db.agent_credentials.c.id))
        if explicit:
            c.execute(explicit_grants.insert().values(credential_id=credential, project_id=team.pid, granted_by=owner.account))
    return {'Authorization': 'Bearer ' + secret}


def test_legacy_report_receipt_and_fact_paths_remain_valid(live, team):
    from pmo.contracts import RULE_VERSION
    from pmo.projects import fingerprint
    from test_facts import medium, post, proposal

    pm, reviewer = (team.users[name] for name in ('pm', 'reviewer'))
    author = team.people['pm']
    token = _token(team, person='pm')
    auth_hash = hashlib.sha256(token['Authorization'].split()[1].encode()).hexdigest()
    key = headers()['Idempotency-Key']
    original_quote = 'Historical independently recorded 7.25 days'
    prior, report_id = datetime(2025, 1, 6, tzinfo=UTC), uuid4()
    original_entry = {'summary': 'Historical note', 'legacy/custom': {'source~line': original_quote}}
    original_fields = sections(requirement_progress=[original_entry])
    receipt_body = body(structuredFields=original_fields, narrative='')
    digest = fingerprint(['report-v1', str(team.pid), str(author.id), 'weekly', WEEK, 1, original_fields, ''])
    reference = f'report:{report_id}@1'
    initial = {'data': {'reportId': str(report_id), 'projectId': str(team.pid), 'type': 'weekly', 'period': WEEK,
        'status': 'submitted', 'authorId': str(author.id), 'revision': 1, 'submittedAt': '2025-01-06T00:00:00Z',
        'structuredFields': original_fields}, 'meta': {'operationId': SUBMIT, 'asOf': '2025-01-06T00:00:00Z',
        'sourceRefs': [reference], 'ruleVersion': RULE_VERSION}}
    with team.engine.begin() as c:
        credential = c.scalar(sa.select(db.agent_credentials.c.id).where(db.agent_credentials.c.token_hash == auth_hash))
        c.execute(reports.insert().values(id=report_id, project_id=team.pid, created_by=author.id,
            author_id=author.id, type='weekly', period=WEEK, period_start=START, period_end=END, version_no=1,
            is_current=True, is_current_confirmed=False, status='submitted', structured_fields=original_fields,
            narrative='', content_hash=digest, submitted_by=author.id, submitted_at=prior, updated_at=prior))
        c.execute(db.idempotency_keys.insert().values(actor_id=credential, actor_type='agent', org_id=author.org,
            delegated_by=author.id, idempotency_key=key, operation=SUBMIT, completed_at=prior,
            expires_at=datetime.now(UTC)+timedelta(days=1), request_hash=fingerprint([str(team.pid), receipt_body]),
            response_body=initial, response_hash=fingerprint(initial), response_status=200, status='completed',
            resource_id=report_id))

    path = f'reports/{report_id}'
    view = data(pm.get(path))
    assert view['contentHash'] == digest and view['legacyStructuredFields'] is True
    assert view['structuredFields']['requirement_progress'] == [{'summary': 'Historical note'}]
    fact = data(post(pm, view, proposal(view, value='7.25', quote=original_quote)))
    assert fact['quotePath'] == '/structuredFields/requirement_progress/0/legacy~1custom/source~0line'
    published = data(pm.post(path + '/confirm', headers=headers(1), json={}))
    assert data(medium(reviewer, fact))['contentHash'] == published['contentHash'] == digest
    rest = live[1].post('/api/v1/' + team.path, headers={**token, 'Idempotency-Key': key}, json=receipt_body)
    assert rest.status_code == 200 and rest.json() == initial

    config = StdioServerParameters(command=sys.executable, args=['-m', 'pmo.mcp_server'],
        env={'PMO_API_PORT': str(live[2]), 'PMO_AGENT_TOKEN': token['Authorization'].split()[1], 'PYTHONUTF8': '1'})

    async def replay_only():
        args = {**receipt_body, 'projectId': str(team.pid), 'idempotencyKey': key}
        async with stdio_client(config) as streams, ClientSession(*streams) as session:
            await session.initialize()
            replayed = await session.call_tool(SUBMIT, args)
            assert not replayed.isError
            assert replayed.structuredContent == {'ok': True, **initial,
                'data': {**initial['data'], 'structuredFields': view['structuredFields']}}
            for changes, code in (({'idempotencyKey': uuid4().hex}, 'VALIDATION_FAILED'),
                                  ({'narrative': 'Not the original'}, 'IDEMPOTENCY_CONFLICT')):
                denied = await session.call_tool(SUBMIT, {**args, **changes})
                assert denied.isError and denied.structuredContent['error']['code'] == code
    asyncio.run(replay_only())
    with team.engine.connect() as c:
        assert c.execute(sa.select(reports.c.content_hash, reports.c.structured_fields, reports.c.revision)
                          .where(reports.c.project_id == team.pid)).one() == (digest, original_fields, 2)
        keys = db.idempotency_keys.c
        assert c.scalar(sa.select(keys.response_body).where(keys.actor_id == credential, keys.idempotency_key == key)) == initial


def test_report_related_reads_use_the_same_resource_scope(live, team):
    from test_evidence import binary

    pm, author, peer, reviewer = (team.users[k] for k in ('pm', 'reporter', 'outsider', 'reviewer'))
    peer_id = team.people['outsider'].id
    with team.engine.begin() as c:
        c.execute(db.project_members.insert().values(project_id=team.pid, person_id=peer_id, role='member',
            assigned_by=team.people['pm'].id, assignment_status='accepted', accepted_at=sa.func.now()))
    mine = data(peer.post(team.path, headers=headers(), json=body(narrative='Own report')))
    published = data(author.post(team.path, headers=headers(), json=body(narrative='Another member report')))
    path = f"reports/{published['reportId']}"
    data(pm.post(path + '/confirm', headers=headers(1), json={}))
    newer = data(author.post(team.path, headers=headers(), json=body(narrative='Unpublished report')))
    draft = data(author.post(team.path+'/drafts', headers=headers(), json=body(narrative='Private draft')))

    listing = peer.get(team.path, params={'pageSize': 1})
    assert data(listing)['total'] == 1 and [i['reportId'] for i in data(listing)['items']] == [mine['reportId']]
    assert listing.json()['meta']['sourceRefs'] == [f"report:{mine['reportId']}@1"]
    for item in (published, newer, draft):
        target = f"reports/{item['reportId']}"
        deny(peer.get(target), 404, 'RESOURCE_NOT_FOUND')
        deny(peer.get(target + '/facts'), 404, 'RESOURCE_NOT_FOUND')
    assert data(pm.get(team.path))['total'] == 4
    assert data(reviewer.get(team.path))['total'] == 3
    assert data(reviewer.get(path))['narrative'] == 'Another member report'
    deny(reviewer.get(f"reports/{draft['reportId']}"), 404, 'RESOURCE_NOT_FOUND')

    evidence_path = f'projects/{team.pid}/evidence'
    pdf = binary('pdf')
    form = {'linkedResourceType': 'report', 'linkedResourceId': published['reportId']}
    params = {'reportId': published['reportId']}
    deny(peer.post(evidence_path, headers=headers(1), data=form, files={'file': ('study.pdf', pdf, 'application/pdf')}),
         404, 'RESOURCE_NOT_FOUND')
    file = data(author.post(evidence_path, headers=headers(1), data=form, files={'file': ('study.pdf', pdf, 'application/pdf')}))
    file_path = f"evidence/{file['evidenceId']}"
    assert file['sha256'] == hashlib.sha256(pdf).hexdigest()
    assert data(peer.get(evidence_path))['total'] == data(peer.get(evidence_path, params=params))['total'] == 0
    deny(peer.get(file_path), 404, 'RESOURCE_NOT_FOUND')
    deny(peer.get(file_path, params={'download': True}), 404, 'RESOURCE_NOT_FOUND')
    assert data(reviewer.get(evidence_path, params=params))['items'][0]['evidenceId'] == file['evidenceId']
    assert reviewer.get(file_path, params={'download': True}).content == pdf

    auth = _token(team, person='outsider', actions=(LIST, GET))
    assert data(live[1].get('/api/v1/'+team.path, headers=auth))['total'] == 1
    deny(live[1].get('/api/v1/'+path, headers=auth), 404, 'RESOURCE_NOT_FOUND')

    async def flow():
        params = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'pmo.mcp_server'],
            env={'PMO_API_PORT': str(live[2]), 'PMO_AGENT_TOKEN': auth['Authorization'].split(' ', 1)[1], 'PYTHONUTF8': '1'})
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.call_tool(LIST, {'projectId': str(team.pid), 'pageSize': 1})
            assert not listed.isError and listed.structuredContent['data']['total'] == 1
            assert listed.structuredContent['data']['items'][0]['reportId'] == mine['reportId']
            refused = await session.call_tool(GET, {'reportId': published['reportId']})
            assert refused.isError and refused.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND'

    asyncio.run(flow())


def test_legacy_report_marker_is_not_a_publication_receipt(live, team):
    pm = team.people['pm'].id
    with team.engine.begin() as c:
        legacy = c.scalar(db.reports.insert().values(project_id=team.pid, created_by=pm, author_id=pm,
            type='weekly', period=WEEK, period_start=START, period_end=END, version_no=1, status='confirmed',
            is_current=True, is_current_confirmed=True, structured_fields={}, narrative='Legacy notes preserved',
            submitted_at=datetime(2025, 1, 6, tzinfo=UTC)).returning(db.reports.c.id))
    path = f'reports/{legacy}'
    browser = data(team.users['pm'].get(path))
    assert browser['status'] == 'confirmed' and browser['narrative'] == 'Legacy notes preserved'
    assert not browser['isCurrentConfirmed'] and 'confirmedAt' not in browser
    assert receipt(team, as_of=datetime.now(UTC)) == []
    auth = _token(team, person='pm', actions=(LIST, GET))
    assert data(live[1].get('/api/v1/'+path, headers=auth))['isCurrentConfirmed'] is False
    [listed] = data(live[1].get('/api/v1/'+team.path, headers=auth))['items']
    assert listed['reportId'] == str(legacy) and listed['isCurrentConfirmed'] is False
    with team.engine.connect() as c:
        assert c.scalar(sa.select(reports.c.is_current_confirmed).where(reports.c.id == legacy)) is True


def test_agent_scope_and_closed_mcp_dtos(team):
    from pmo.mcp_server import tools
    token = _token(team, actions=(LIST, GET, SUBMIT, CONFIRM))
    pm = team.users['pm']
    draft = data(pm.post(team.path + '/drafts', headers=headers(), json=body(narrative='Draft note')))
    with httpx.Client(base_url=team.url, trust_env=False) as agent:
        deny(agent.post(team.path, headers={**_token(team, actions=(LIST,)), **headers()}, json=body()), 403, 'FORBIDDEN')
        deny(agent.post(team.path, headers={**_token(team, explicit=False), **headers()}, json=body()), 404, 'RESOURCE_NOT_FOUND')
        deny(agent.post(team.path, headers={**_token(team, person='reviewer'), **headers()}, json=body()), 403, 'FORBIDDEN')
        deny(agent.post(f'projects/{team.other}/reports', headers={**token, **headers()}, json=body()), 404, 'RESOURCE_NOT_FOUND')
        deny(agent.get(f"reports/{draft['reportId']}", headers=token), 404, 'RESOURCE_NOT_FOUND')
        deny(agent.post(team.path, headers={**token, **headers()}, json=body(authorId=str(team.people['pm'].id))), 422, 'VALIDATION_FAILED')
        result = agent.post(team.path, headers={**token, **headers()}, json=body(narrative='Delegated private narrative'))
        item = data(result)
        assert item['status'] == 'submitted' and item['authorId'] == str(team.people['reporter'].id)
        path = f"reports/{item['reportId']}"
        jsonschema.validate({'ok': True, **result.json()}, tools[SUBMIT].outputSchema)
        found = agent.get(path, headers=token)
        assert 'narrative' not in data(found)
        jsonschema.validate({'ok': True, **found.json()}, tools[GET].outputSchema)
        result = agent.get(team.path, headers=token)
        assert data(result)['total'] == 1
        jsonschema.validate({'ok': True, **result.json()}, tools[LIST].outputSchema)
        deny(agent.post(path + '/confirm', headers={**token, **headers(1)}, json={}), 403, 'FORBIDDEN')
    assert data(pm.get(path))['narrative'] == 'Delegated private narrative'
    with team.engine.connect() as c:
        audit = c.execute(sa.select(db.audit_logs).where(db.audit_logs.c.project_id == team.pid,
            db.audit_logs.c.action == SUBMIT, db.audit_logs.c.result == 'success')).mappings().one()
        assert audit.actor_type == 'agent' and audit.delegated_by == team.people['reporter'].account


def test_report_real_stdio_human_publish_and_distinct_idempotency_actors(live, team):
    author, pm = team.users['reporter'], team.users['pm']
    with team.engine.begin() as c:
        c.execute(db.user_roles.insert().values(user_id=team.people['reporter'].account, role='delegator'))
    collection = data(author.get('me/agent-tokens'))
    granted = author.post('me/agent-tokens', headers=headers(collection['collectionRevision']), json={
        'allowedProjectIds': [str(team.pid)], 'allowedActions': [SUBMIT, GET, LIST],
        'expiresAt': (datetime.now(UTC) + timedelta(minutes=10)).isoformat()})
    credential = data(granted)
    token = granted.headers['X-PMO-New-Token']

    async def flow():
        config = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'pmo.mcp_server'], env={
            'PMO_API_PORT': str(live[2]), 'PMO_AGENT_TOKEN': token, 'PYTHONUTF8': '1'})
        async with stdio_client(config) as streams, ClientSession(*streams) as session:
            await session.initialize()
            schemas = {tool.name: tool.outputSchema for tool in (await session.list_tools()).tools}
            assert {SUBMIT, GET, LIST} <= schemas.keys() and CONFIRM not in schemas

            async def call(name, args):
                response = await session.call_tool(name, args)
                assert not response.isError, response
                jsonschema.validate(response.structuredContent, schemas[name])
                return response.structuredContent

            doc = body(narrative='Report produced under the named author delegation')
            args = dict(doc, projectId=str(team.pid), idempotencyKey=uuid4().hex)
            sent = await call(SUBMIT, args)
            item = sent['data']
            path = f"reports/{item['reportId']}"
            assert item['status'] == 'submitted' and 'narrative' not in item
            assert await call(SUBMIT, args) == sent
            rejected = await session.call_tool(SUBMIT, dict(args, narrative='The same key cannot change the document'))
            assert rejected.isError and rejected.structuredContent['error']['code'] == 'IDEMPOTENCY_CONFLICT'
            web = data(pm.get(path))
            found = await call(GET, {'reportId': item['reportId']})
            assert found['data']['structuredFields'] == web['structuredFields']
            assert found['meta']['sourceRefs'] == [f"report:{web['reportId']}@{web['revision']}"]
            assert (await call(LIST, {'projectId': str(team.pid)}))['data']['total'] == 1

            # Same delegated natural person, operation, key and body, but a distinct actor namespace.
            human = data(author.post(team.path, headers={'Idempotency-Key': args['idempotencyKey']}, json=doc))
            assert human['reportId'] != web['reportId'] and human['versionNo'] == 2
            deny(author.post(path + '/confirm', headers=headers(web['revision']), json={}), 403, 'FORBIDDEN')
            deny(live[1].post('/api/v1/' + path + '/confirm', headers={**headers(web['revision']),
                'Authorization': 'Bearer ' + token}, json={}), 403, 'FORBIDDEN')
            published = data(pm.post(path + '/confirm', headers=headers(web['revision']), json={}))
            assert published['isCurrentConfirmed'] and published['confirmedBy'] == str(team.people['pm'].id)
            assert (await call(GET, {'reportId': item['reportId']}))['data']['revision'] == published['revision']

            daily = data(author.post(team.path, headers=headers(), json=body(reportType='daily', period='2025-01-05')))
            for kind, page, count, expected in (
                ('weekly', 1, 2, [human['reportId']]), ('weekly', 2, 2, [item['reportId']]),
                ('weekly', 3, 2, []), ('weekly', 10**21, 2, []), ('daily', 1, 1, [daily['reportId']]),
            ):
                params = {'reportType': kind, 'page': page, 'pageSize': 1}
                result = await call(LIST, {'projectId': str(team.pid), **params})
                direct = live[1].get('/api/v1/' + team.path, headers={'Authorization': 'Bearer '+token}, params=params)
                assert {k: v for k, v in result['data'].items() if k != 'asOf'} == {
                    k: v for k, v in data(direct).items() if k != 'asOf'}
                assert result['meta']['sourceRefs'] == direct.json()['meta']['sourceRefs']
                assert (result['data']['page'], result['data']['pageSize'], result['data']['total']) == (page, 1, count)
                assert [entry['reportId'] for entry in result['data']['items']] == expected
            bad = await session.call_tool(LIST, {'projectId': str(team.pid), 'reportType': 'monthly'})
            assert bad.isError and bad.structuredContent['error']['code'] == 'VALIDATION_FAILED'

            current = data(pm.post(f"reports/{human['reportId']}/confirm", headers=headers(human['revision']), json={}))
            history = (await call(LIST, {'projectId': str(team.pid), 'reportType': 'weekly'}))['data']['items']
            assert [(entry['reportId'], entry['isCurrentConfirmed']) for entry in history] == [
                (current['reportId'], True), (published['reportId'], False)]
            for entry in history:
                found = (await call(GET, {'reportId': entry['reportId']}))['data']
                web = data(pm.get(f"reports/{entry['reportId']}"))
                assert found['isCurrentConfirmed'] == entry['isCurrentConfirmed'] == web['isCurrentConfirmed']
                assert found['revision'] == entry['revision'] == web['revision']
                assert 'isCurrent' not in found and 'confirmedBy' not in found and 'narrative' not in found
            assert data(pm.get(path))['confirmedAt'] == published['confirmedAt']
            encore = await call(SUBMIT, args)
            assert encore == sent and 'isCurrentConfirmed' not in encore['data']

            other = await session.call_tool(LIST, {'projectId': str(team.other)})
            assert other.isError and other.structuredContent['error']['code'] == 'RESOURCE_NOT_FOUND'
            data(author.post(f"me/agent-tokens/{credential['tokenId']}/revoke", headers=headers(credential['revision']),
                             json={'reason': 'Synthetic test complete'}))
            revoked = await session.call_tool(GET, {'reportId': item['reportId']})
            assert revoked.isError and revoked.structuredContent['error']['code'] == 'UNAUTHORIZED'

    asyncio.run(flow())
    with pytest.raises(DBAPIError), team.engine.begin() as c:
        c.execute(reports.update().where(reports.c.project_id == team.pid).values(narrative='Forbidden history mutation'))


def test_lr06_structure_and_project_archive(team):
    pm, author, other = (team.users[k] for k in ('pm', 'reporter', 'outsider'))
    good, foreign = map(str, team.milestones)
    typed = sections(milestone_progress=[{'claim': 'completion', 'milestoneId': foreign, 'summary': 'Unverified'}])
    deny(author.post(team.path, headers=headers(), json=body(structuredFields=typed)), 422, 'REPORT_SOURCE_SCOPE')
    typed['milestone_progress'][0]['milestoneId'] = good
    item = data(author.post(team.path, headers=headers(), json=body(structuredFields=typed)))
    path = f"reports/{item['reportId']}"
    deny(other.get(path), 404, 'RESOURCE_NOT_FOUND')
    deny(other.post(path + '/confirm', headers=headers(1), json={}), 404, 'RESOURCE_NOT_FOUND')
    data(pm.post(path + '/confirm', headers=headers(1), json={}))
    data(pm.post(team.path, headers=headers(), json=body(reportType='daily', period=START.isoformat())))
    seen = receipt(team, as_of=datetime.now(UTC))
    assert len(seen) == 1 and seen[0].source_ref == f"report:{item['reportId']}@1"
    assert seen[0].period_start == START and seen[0].period_end == END
    claim = seen[0].milestone_claims[0]
    assert claim.milestone_id == UUID(good) and claim.kind == 'completion' and claim.evidence_refs == ()
    with team.engine.begin() as c:
        assert c.scalar(sa.select(db.milestones.c.actual_date).where(db.milestones.c.id == UUID(good))) is None
        assert not c.scalar(sa.select(sa.func.count()).select_from(db.facts).where(db.facts.c.project_id == team.pid))
        c.execute(db.projects.update().where(db.projects.c.id == team.pid).values(stage='archived'))
    assert not data(pm.get(team.path))['canCreateDraft']
    assert data(pm.get(path))['isCurrentConfirmed']
    deny(pm.post(team.path, headers=headers(), json=body()), 422, 'PROJECT_ARCHIVED')
    deny(pm.post(team.path + '/drafts', headers=headers(), json=body()), 422, 'PROJECT_ARCHIVED')
    deny(pm.post(path + '/confirm', headers=headers(2), json={}), 422, 'PROJECT_ARCHIVED')


def test_parallel_versions_and_failed_publish_roll_back(team):
    pm, author = (team.users[k] for k in ('pm', 'reporter'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = [pool.submit(client.post, team.path, headers=headers(), json=body()) for client in (pm, author)]
        first, second = sorted((data(f.result()) for f in replies), key=lambda i: i['versionNo'])
    assert (first['versionNo'], second['versionNo']) == (1, 2)
    before = f"reports/{first['reportId']}"
    after = f"reports/{second['reportId']}"
    data(pm.post(before + '/confirm', headers=headers(1), json={}))
    with team.engine.begin() as c:
        c.exec_driver_sql(f"""CREATE FUNCTION test_block_report_publish() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.event_type='{CONFIRM}' AND NEW.payload->>'projectId'='{team.pid}' THEN
            RAISE EXCEPTION 'synthetic report outbox failure'; END IF; RETURN NEW; END $$""")
        c.exec_driver_sql('CREATE TRIGGER test_block_report_publish BEFORE INSERT ON outbox_events '
                          'FOR EACH ROW EXECUTE FUNCTION test_block_report_publish()')
    key = headers(1)
    try:
        deny(pm.post(after + '/confirm', headers=key, json={}), 500, 'DATABASE_FAILURE')
        assert data(pm.get(before))['isCurrentConfirmed'] and data(pm.get(after))['status'] == 'submitted'
    finally:
        with team.engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER test_block_report_publish ON outbox_events')
            c.exec_driver_sql('DROP FUNCTION test_block_report_publish()')
    assert data(pm.post(after + '/confirm', headers=key, json={}))['isCurrentConfirmed']
    with team.engine.connect() as c:
        rows = list(c.execute(sa.select(reports).where(reports.c.project_id == team.pid)).mappings())
        assert sum(row.is_current_confirmed for row in rows) == sum(row.is_current for row in rows) == 1
