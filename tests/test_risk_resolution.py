from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from textwrap import wrap
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from argon2 import PasswordHasher
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pmo import schema_v1 as db
from pmo.evidence import links
from pmo.identity import Actor, explicit_grants
from pmo.risk_resolution import ACCEPTANCE, ACCEPTANCE_LIMIT, CLOSE, GET, HANDOFF, acceptances, resolutions, resolve_risk, router
from pmo.risks import ESCALATE


SCOPE = 'Monitoring and escalation for the existing site only'
TEST_RISK = UUID('10000000-0000-4000-8000-000000000001')
TEST_RECEIVER = UUID('10000000-0000-4000-8000-000000000002')
TEST_ACCEPTED = datetime(2020, 1, 2, 12, 0, tzinfo=UTC)
TEST_EXCERPT = f'I explicitly accept risk {TEST_RISK} revision 1 as receiver {TEST_RECEIVER} for scope: {SCOPE}'


def _pdf(text):
    # Deterministic test content, never a real signature or a product receipt.
    writer = PdfWriter()
    page = writer.add_blank_page(580, 300)
    name = NameObject
    page[name('/Resources')] = DictionaryObject({name('/Font'): DictionaryObject({name('/F1'): DictionaryObject({
        name('/Type'): name('/Font'), name('/Subtype'): name('/Type1'), name('/BaseFont'): name('/Courier'),
    })})})
    stream = DecodedStreamObject()
    lines = ['SYNTHETIC TEST ONLY - NOT A SIGN-OFF', *wrap(text, 72, break_on_hyphens=False)]
    stream.set_data(('BT /F1 9 Tf 36 260 Td 14 TL\n' + '\n'.join(f'({line}) Tj T*' for line in lines) + '\nET').encode('ascii'))
    page[name('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def headers(revision=1):
    return {'Idempotency-Key': uuid4().hex, 'If-Match': str(revision)}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


def rejected(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()['code'] == code, response.text
    assert response.json()['requestId'] == response.headers['X-Request-ID']
    return response.headers['X-Request-ID']


@pytest.fixture
def scenario(live, monkeypatch):
    engine, api, port, env = live
    monkeypatch.setenv('PMO_EVIDENCE_DIR', env['PMO_EVIDENCE_DIR'])
    password = token_urlsafe()
    hashed = PasswordHasher().hash(password)
    people, users, names = {}, {}, {}
    with engine.begin() as c:
        org, other_org = [c.scalar(db.organizations.insert().values(name='Synthetic risk test', status='active')
            .returning(db.organizations.c.id)) for _ in range(2)]
        for role in ('owner', 'reviewer', 'second_reviewer', 'receiver', 'outsider', 'foreign'):
            people[role], names[role] = uuid4(), uuid4().hex
            users[role] = c.scalar(db.user_accounts.insert().values(username=names[role], password_hash=hashed,
                is_active=True).returning(db.user_accounts.c.id))
            c.execute(db.persons.insert().values(id=people[role], created_by=people[role], user_id=users[role],
                org_id=other_org if role == 'foreign' else org, name=role, is_active=True))
        owner = people['owner']
        projects = [c.scalar(db.projects.insert().values(code=uuid4().hex, name='Synthetic risk project', org_id=org,
            created_by=owner, initiator_id=owner, pm_id=owner, stage='delivering', revision=1).returning(db.projects.c.id))
            for _ in range(2)]
        for pid in projects:
            for who, role in (('owner', 'pm'), ('owner', 'reviewer'), ('reviewer', 'reviewer'), ('second_reviewer', 'reviewer')):
                c.execute(db.project_members.insert().values(project_id=pid, person_id=people[who], assigned_by=owner,
                    role=role, assignment_status='accepted', accepted_at=sa.func.now()))
        secret = token_urlsafe()
        digest = sha256(secret.encode()).hexdigest()
        account = users['reviewer']
        cid = c.scalar(db.agent_credentials.insert().values(delegated_by=account, org_id=org,
            allowed_actions=[CLOSE, HANDOFF, ACCEPTANCE, ESCALATE], allowed_project_ids=[str(projects[0])], status='active',
            token_hash=digest, token_fingerprint=digest[:12], expires_at=datetime.now(UTC) + timedelta(minutes=10))
            .returning(db.agent_credentials.c.id))
        c.execute(explicit_grants.insert().values(credential_id=cid, project_id=projects[0], granted_by=account))

    def risk(pid=projects[0], *, risk_id=None, created_at=None):
        with engine.begin() as c:
            fields = dict(id=risk_id or uuid4(), project_id=pid, created_by=owner, owner_id=owner,
                statement='Synthetic supplier delay', prob=4, impact=5, response='Inspect after mitigation',
                is_blocking=True, blocked_since=sa.func.now(), status='open')
            if created_at:
                fields.update(created_at=created_at, updated_at=created_at)
            return c.execute(db.risks.insert().values(**fields).returning(db.risks)).mappings().one()

    def effective(row):
        with engine.begin() as c:
            project = c.execute(sa.select(db.projects).where(db.projects.c.id == row.project_id)).mappings().one()
            current = c.execute(sa.select(db.risks).where(db.risks.c.id == row.id)).mappings().one()
            a = Actor(users['owner'], owner, users['owner'], org, frozenset(), (), False, 'human')
            return resolve_risk(c, a, project, current)

    with ExitStack() as stack:
        def session(role):
            client = stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/', trust_env=False))
            login = client.post('auth/login', json={'username': names[role], 'password': password})
            assert login.status_code == 200, login.text
            client.headers['X-CSRF-Token'] = login.json()['csrfToken']
            return client

        clients = {role: session(role) for role in people}

        def upload(row, data=None, *, verified=True, uploader='owner', verifier='reviewer'):
            path = f'projects/{row.project_id}/evidence'
            uploader_client = clients[uploader]
            revision = ok(uploader_client.get(path))['collectionRevision']
            if data is None:
                data = _pdf('Synthetic mitigation inspection; no operational sign-off.')
            item = ok(uploader_client.post(path, headers=headers(revision), files={'file': ('fixture.pdf', data, 'application/pdf')},
                data={'linkedResourceType': 'risk', 'linkedResourceId': str(row.id), 'description': 'Synthetic test only'}))
            if verified:
                item = verify(item, verifier)
            return item

        def verify(item, verifier='reviewer'):
            return ok(clients[verifier].post(f'evidence/{item["evidenceId"]}/verify', headers=headers(item['revision']),
                json={'reason': 'Compared the synthetic fixture with its test origin', 'sha256': item['sha256']}))

        yield SimpleNamespace(engine=engine, api=api, dir=Path(env['PMO_EVIDENCE_DIR']), project=projects[0],
            other=projects[1], org=org, people=people, bearer={'Authorization': 'Bearer ' + secret}, clients=clients,
            risk=risk, upload=upload, verify=verify, effective=effective)


def test_frozen_resolution_dtos():
    app = FastAPI()
    app.include_router(router)
    spec = app.openapi()
    for action, operation, fields in [('close', CLOSE, {'resolutionEvidenceRef'}),
            ('handoff', HANDOFF, {'receiverPersonId', 'acceptanceEvidenceRef'})]:
        post = spec['paths'][f'/api/v1/risks/{{riskId}}/{action}']['post']
        assert post['operationId'] == operation and post['x-actorTypes'] == ['human']
        assert {p['name'] for p in post['parameters'] if p['in'] == 'header' and p['required']} == {'If-Match', 'Idempotency-Key'}
        ref = post['requestBody']['content']['application/json']['schema']['$ref']
        body = spec['components']['schemas'][ref.rsplit('/', 1)[-1]]
        assert set(body['required']) == set(body['properties']) == fields and not body['additionalProperties']
    read = spec['paths']['/api/v1/risks/{riskId}/resolution']['get']
    assert read['operationId'] == GET and read['x-actorTypes'] == ['human']


def test_escalation_preserves_owner_starts_clock_and_requires_independent_resolution(scenario):
    s = scenario
    owner, reviewer, outsider = (s.clients[name] for name in ('owner', 'reviewer', 'outsider'))
    project_path = f'projects/{s.project}'
    created = ok(owner.post(project_path + '/risks', headers=headers(), json={
        'statement': 'Supplier data format may delay the milestone', 'response': 'Obtain a replacement export',
        'prob': 2, 'impact': 4, 'isBlocking': False}))
    path = f"risks/{created['riskId']}"
    assert 'blockedSince' not in created
    assert ok(owner.get(project_path + '/risks'))['items'][0]['canEscalate'] is True
    command = {'reason': 'Supplier confirmed the only export is unavailable.'}
    rejected(outsider.post(path + '/escalate', headers=headers(), json=command), 404, 'RESOURCE_NOT_FOUND')
    assert ok(reviewer.get(project_path + '/risks'))['items'][0]['canEscalate'] is False
    rejected(reviewer.post(path + '/escalate', headers=headers(), json=command), 403, 'FORBIDDEN')
    rejected(s.api.post('/api/v1/' + path + '/escalate', headers={**s.bearer, **headers()}, json=command), 403, 'FORBIDDEN')
    rejected(owner.post(path + '/escalate', headers=headers(2), json=command), 409, 'VERSION_CONFLICT')
    rejected(owner.post(path + '/escalate', headers=headers(), json={'reason': '   '}), 422, 'VALIDATION_FAILED')

    key = headers()
    written = owner.post(path + '/escalate', headers=key, json=command)
    promoted = ok(written)
    assert promoted['status'] == 'open' and promoted['isBlocking']
    assert promoted['revision'] == 2 and promoted['ownerId'] == created['ownerId'] == promoted['escalatedBy']
    assert promoted['escalationReason'] == command['reason']
    assert owner.post(path + '/escalate', headers=key, json=command).json() == written.json()
    rejected(owner.post(path + '/escalate', headers=key, json={'reason': 'Another source is unavailable.'}),
             409, 'IDEMPOTENCY_CONFLICT')
    rejected(owner.post(path + '/escalate', headers=headers(2), json=command), 422, 'RISK_ALREADY_BLOCKING')
    reread = ok(owner.get(project_path + '/risks'))['items'][0]
    assert reread['blockedSince'] == promoted['blockedSince'] and reread['canEscalate'] is False
    assert 'BLOCKING_RISK_UNRESOLVED' in {gap['code'] for gap in ok(owner.get(project_path + '/closing/check'))['missing']}

    with s.engine.begin() as c:
        risk = c.execute(sa.select(db.risks).where(db.risks.c.id == created['riskId'])).mappings().one()
        assert risk.created_at <= risk.blocked_since == risk.updated_at
        logs = c.execute(sa.text('''SELECT source_refs FROM audit_logs WHERE project_id=:pid AND action=:op AND result='success' '''),
                         {'pid': s.project, 'op': ESCALATE}).scalars().all()
        assert logs == [[f"risk:{created['riskId']}@1", f"risk:{created['riskId']}@2"]]
        assert c.scalar(sa.select(sa.func.count()).select_from(db.outbox_events)
                        .where(db.outbox_events.c.dedupe_key == written.headers['X-Request-ID'])) == 1
        receipt = c.scalar(sa.select(db.idempotency_keys.c.response_body).where(
            db.idempotency_keys.c.resource_id == created['riskId'], db.idempotency_keys.c.operation == ESCALATE))
        assert receipt['data']['escalationReason'] == command['reason']
    evidence = s.upload(risk)
    rejected(owner.post(path + '/close', headers=headers(2), json={'resolutionEvidenceRef': evidence['evidenceRef']}), 403,
             'INDEPENDENT_REVIEWER_REQUIRED')
    assert ok(reviewer.post(path + '/close', headers=headers(2), json={
        'resolutionEvidenceRef': evidence['evidenceRef']}))['status'] == 'closed'
    rejected(owner.post(path + '/escalate', headers=headers(3), json=command), 422, 'RISK_REGISTER_LOCKED')


def test_independent_close_and_gate_recheck(scenario):
    s = scenario
    row = s.risk()
    reviewer, owner, outsider = (s.clients[who] for who in ('reviewer', 'owner', 'outsider'))
    path = f'risks/{row.id}/close'
    item = s.upload(row, verified=False)
    command = {'resolutionEvidenceRef': item['evidenceRef']}
    rejected(reviewer.post(path, headers=headers(), json={}), 422, 'VALIDATION_FAILED')
    rejected(reviewer.post(path, headers=headers(), json={**command, 'closed': True}), 422, 'VALIDATION_FAILED')
    rejected(s.api.post('/api/v1/' + path, headers=headers(), json=command), 401, 'UNAUTHORIZED')
    rejected(outsider.post(path, headers=headers(), json=command), 404, 'RESOURCE_NOT_FOUND')
    denied = rejected(owner.post(path, headers=headers(), json=command), 403, 'INDEPENDENT_REVIEWER_REQUIRED')
    rejected(s.api.post('/api/v1/' + path, headers={**headers(), **s.bearer}, json=command), 403, 'FORBIDDEN')
    rejected(reviewer.post(path, headers=headers(), json=command), 422, 'VERIFIED_EVIDENCE_REQUIRED')
    rejected(reviewer.post(path, headers=headers(2), json=command), 409, 'VERSION_CONFLICT')
    wrong_target = s.upload(s.risk())
    cross_project = s.upload(s.risk(s.other))
    rejected(reviewer.post(path, headers=headers(), json={'resolutionEvidenceRef': wrong_target['evidenceRef']}), 422, 'EVIDENCE_TARGET_MISMATCH')
    rejected(reviewer.post(path, headers=headers(), json={'resolutionEvidenceRef': cross_project['evidenceRef']}), 404, 'RESOURCE_NOT_FOUND')
    own_proof = s.upload(row, uploader='reviewer', verifier='second_reviewer')
    rejected(reviewer.post(path, headers=headers(), json={'resolutionEvidenceRef': own_proof['evidenceRef']}), 403, 'INDEPENDENT_REVIEWER_REQUIRED')
    item = s.verify(item)

    key = headers()
    first = reviewer.post(path, headers=key, json=command)
    data = ok(first)
    assert (data['status'], data['revision'], data['evidenceRevision'], data['evidenceSha256']) == ('closed', 2, 2, item['sha256'])
    assert data['closedAt'] and data['isBlocking'] and data['blockedSince']
    assert reviewer.post(path, headers=key, json=command).json() == first.json()
    rejected(reviewer.post(path, headers=key, json={'resolutionEvidenceRef': wrong_target['evidenceRef']}), 409, 'IDEMPOTENCY_CONFLICT')
    receipt = s.effective(row)
    request_id = first.headers['X-Request-ID']
    assert receipt and str(receipt.id) == data['resolutionId'] and str(receipt.request_id) == request_id
    assert (receipt.evidence_sha256, receipt.evidence_revision) == (item['sha256'], 2)
    with s.engine.begin() as c:
        audit = db.audit_logs.c
        assert c.scalar(sa.select(audit.result).where(audit.request_id == request_id)) == 'success'
        assert c.scalar(sa.select(audit.result).where(audit.request_id == denied)) == 'denied'
        outbox = db.outbox_events.c
        events = c.execute(sa.select(outbox.dedupe_key, outbox.payload).where(outbox.event_type == CLOSE,
            outbox.payload['riskId'].astext == str(row.id))).all()
        assert len(events) == 1 and events[0].dedupe_key == request_id and events[0].payload['receiptId'] == data['resolutionId']
        assert c.scalar(sa.select(sa.func.count()).select_from(resolutions).where(resolutions.c.risk_id == row.id)) == 1
        storage_key = c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id == receipt.evidence_id))
    file = s.dir / storage_key
    original = file.read_bytes()
    file.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    assert s.effective(row) is None
    invalid = ok(reviewer.get(f'risks/{row.id}/resolution'))
    assert invalid['status'] == 'unresolved' and 'resolution' not in invalid and not invalid['canClose']
    file.write_bytes(original)

    with s.engine.begin() as c:
        c.execute(links.update().where(links.c.evidence_id == receipt.evidence_id).values(revision=3))
    assert s.effective(row) is None
    with s.engine.begin() as c:
        c.execute(links.update().where(links.c.evidence_id == receipt.evidence_id).values(revision=2))
        c.execute(db.risks.update().where(db.risks.c.id == row.id).values(response='Changed without a resolution'))
    assert s.effective(row) is None
    with s.engine.begin() as c:
        c.execute(db.risks.update().where(db.risks.c.id == row.id).values(response=row.response))
        c.execute(db.projects.update().where(db.projects.c.id == s.project).values(stage='archived'))
    assert s.effective(row).id == receipt.id
    archived = ok(reviewer.get(f'risks/{row.id}/resolution'))
    assert archived['status'] == 'resolved' and archived['resolution']['resolutionId'] == str(receipt.id)
    assert not any(archived[k] for k in ('canClose', 'canRequestAcceptance', 'canVerifyAcceptance'))
    rejected(reviewer.post(f'risks/{wrong_target["linkedResourceId"]}/close', headers=headers(),
        json={'resolutionEvidenceRef': wrong_target['evidenceRef']}), 422, 'RISK_REGISTER_LOCKED')


def test_handoff_pending_and_receiver_confirmation(scenario):
    s = scenario
    row = s.risk()
    path = f'risks/{row.id}/handoff'
    step = path + '/acceptance'
    reviewer, receiver, owner, outsider, foreign = (s.clients[who] for who in ('reviewer', 'receiver', 'owner', 'outsider', 'foreign'))
    item = s.upload(row)
    command = {'receiverPersonId': str(s.people['receiver']), 'acceptanceEvidenceRef': item['evidenceRef']}
    request = {**command, 'kind': 'request', 'acceptanceScope': SCOPE}
    confirm = {**request, 'kind': 'confirm', 'decision': 'accept'}
    for addr, body in ((path, command), (step, request)):
        rejected(s.api.post('/api/v1/' + addr, headers={**headers(), **s.bearer}, json=body), 403, 'FORBIDDEN')
    rejected(reviewer.post(path, headers=headers(), json=command), 422, 'RISK_ACCEPTANCE_REQUIRED')
    rejected(reviewer.post(step, headers=headers(), json={**request, 'receiverPersonId': str(s.people['reviewer'])}), 403, 'INDEPENDENT_REVIEWER_REQUIRED')
    rejected(reviewer.post(step, headers=headers(), json={**request, 'receiverPersonId': str(s.people['foreign'])}), 422, 'INVALID_RISK_RECEIVER')
    rejected(receiver.post(step, headers=headers(), json=confirm), 404, 'RESOURCE_NOT_FOUND')
    pending = ok(reviewer.post(step, headers=headers(), json=request))
    assert pending['kind'] == 'requested' and pending['riskRevision'] == 1
    rejected(reviewer.post(path, headers=headers(), json=command), 422, 'RISK_ACCEPTANCE_REQUIRED')
    assert s.effective(row) is None
    for client in (reviewer, outsider, foreign):
        rejected(client.post(step, headers=headers(), json=confirm), 403, 'RISK_RECEIVER_REQUIRED')
    rejected(receiver.post(step, headers=headers(), json={**confirm, 'decision': True}), 422, 'VALIDATION_FAILED')
    rejected(receiver.post(step, headers=headers(), json={**confirm, 'acceptanceScope': 'A much broader scope'}), 409, 'RISK_ACCEPTANCE_SCOPE_MISMATCH')
    rejected(receiver.get(f'evidence/{item["evidenceId"]}', params={'download': 'true'}), 404, 'RESOURCE_NOT_FOUND')

    assert ok(owner.patch(f'risks/{row.id}', headers=headers(), json={'fieldsRisk': {'response': 'Revised mitigation plan'}}))['revision'] == 2
    rejected(receiver.get(f'risks/{row.id}/resolution'), 404, 'RESOURCE_NOT_FOUND')
    updated = ok(reviewer.get(f'risks/{row.id}/resolution'))
    assert updated['risk']['revision'] == 2 and updated['status'] == 'unresolved' and updated['acceptances'] == []
    rejected(receiver.post(step, headers=headers(), json=confirm), 409, 'VERSION_CONFLICT')
    rejected(receiver.post(step, headers=headers(2), json=confirm), 422, 'RISK_HANDOFF_REQUEST_REQUIRED')
    rejected(reviewer.post(path, headers=headers(2), json=command), 422, 'RISK_ACCEPTANCE_REQUIRED')
    new_request = ok(reviewer.post(step, headers=headers(2), json=request))
    key = headers(2)
    first = receiver.post(step, headers=key, json=confirm)
    accepted = ok(first)
    assert accepted['kind'] == 'receiver_confirmed' and accepted['actorPersonId'] == command['receiverPersonId']
    assert accepted['riskRevision'] == 2 and accepted['acceptanceScope'] == SCOPE
    assert receiver.post(step, headers=key, json=confirm).json() == first.json()
    rejected(receiver.get(f'evidence/{item["evidenceId"]}', params={'download': 'true'}), 404, 'RESOURCE_NOT_FOUND')
    assert s.effective(row) is None  # Confirmation is not independent finalization.
    result = ok(reviewer.post(path, headers=headers(2), json=command))
    assert result['status'] == 'transferred' and result['revision'] == 3 and result['isBlocking']
    assert result['acceptanceId'] == accepted['acceptanceId'] and 'closedAt' not in result
    receipt = s.effective(row)
    assert receipt and str(receipt.receiver_id) == command['receiverPersonId'] and receipt.evidence_sha256 == item['sha256']
    with s.engine.connect() as c:
        parent = c.scalar(sa.select(acceptances.c.request_receipt_id).where(acceptances.c.id == receipt.acceptance_id))
        assert str(parent) == new_request['acceptanceId']


def test_external_verified_acceptance_needs_documented_identity_and_scope(scenario):
    s = scenario
    with s.engine.begin() as c:
        c.execute(db.persons.insert().values(id=TEST_RECEIVER, created_by=s.people['owner'], org_id=s.org,
            name='Synthetic accountless recipient', is_active=True))
    row = s.risk(risk_id=TEST_RISK, created_at=TEST_ACCEPTED - timedelta(days=1))
    reviewer = s.clients['reviewer']
    path = f'risks/{row.id}/handoff'
    step = path + '/acceptance'
    placeholder = s.upload(row)
    item = s.upload(row, _pdf(TEST_EXCERPT))
    command = {'receiverPersonId': str(TEST_RECEIVER), 'acceptanceEvidenceRef': item['evidenceRef']}
    verify = {**command, 'kind': 'verify', 'decision': 'accept', 'acceptanceScope': SCOPE,
        'acceptedAt': TEST_ACCEPTED.isoformat(), 'identityVerification': 'Synthetic test: independently matched receiver identity and source',
        'acceptanceExcerpt': TEST_EXCERPT}
    rejected(reviewer.post(path, headers=headers(), json=command), 422, 'RISK_ACCEPTANCE_REQUIRED')
    rejected(reviewer.post(step, headers=headers(), json={k: v for k, v in verify.items() if k != 'identityVerification'}), 422, 'VALIDATION_FAILED')
    invalid = ({'acceptanceEvidenceRef': placeholder['evidenceRef']}, {'acceptanceScope': 'Monitoring only'},
        {'acceptanceExcerpt': 'I do not accept the current scope: ' + SCOPE}, {'receiverPersonId': str(s.people['receiver'])},
        {'acceptedAt': (datetime.now(UTC) + timedelta(days=1)).isoformat()})
    for change in invalid:
        rejected(reviewer.post(step, headers=headers(), json={**verify, **change}), 422, 'EXPLICIT_RISK_ACCEPTANCE_EVIDENCE_REQUIRED')
    accepted = ok(reviewer.post(step, headers=headers(), json=verify))
    assert accepted['kind'] == 'reviewer_verified' and 'identityVerification' not in accepted and 'acceptanceExcerpt' not in accepted
    result = ok(reviewer.post(path, headers=headers(), json=command))
    assert result['acceptanceId'] == accepted['acceptanceId'] and result['receiverPersonId'] == str(TEST_RECEIVER)
    assert s.effective(row).kind == 'transferred'


def test_resolution_concurrent_writes_and_unbacked_terminal_status(scenario):
    s = scenario
    row = s.risk()
    target = s.risk()
    item = s.upload(row)
    command = {'resolutionEvidenceRef': item['evidenceRef']}
    path = f'risks/{row.id}/close'
    with ThreadPoolExecutor(2) as pool:
        writes = list(pool.map(lambda actor: s.clients[actor].post(path, headers=headers(), json=command),
            ('reviewer', 'second_reviewer')))
    assert sorted(w.status_code for w in writes) == [200, 409]
    assert s.effective(row) is not None
    with s.engine.begin() as c:
        c.execute(db.risks.update().where(db.risks.c.id == target.id).values(status='closed', closed_at=sa.func.now()))
    assert s.effective(target) is None


def test_resolution_read_scopes_nominees_and_reports_effective_capabilities(scenario):
    s = scenario
    risk = s.risk()
    other = s.risk(s.other)
    reviewer, owner, receiver, outsider, foreign = (s.clients[k] for k in ('reviewer', 'owner', 'receiver', 'outsider', 'foreign'))
    base = f'risks/{risk.id}'
    path = base + '/resolution'
    rejected(s.api.get('/api/v1/' + path), 401, 'UNAUTHORIZED')
    rejected(s.api.get('/api/v1/' + path, headers=s.bearer), 403, 'FORBIDDEN')
    for client in (receiver, outsider, foreign):
        rejected(client.get(path), 404, 'RESOURCE_NOT_FOUND')

    caps = ('canClose', 'canRequestAcceptance', 'canVerifyAcceptance')
    member = ok(owner.get(path))
    assert member['audience'] == 'project' and member['status'] == 'unresolved' and member['acceptances'] == []
    assert member['risk']['statement'] == risk.statement and member['risk']['projectId'] == str(s.project)
    assert not any(member[k] for k in caps)
    assert all(ok(reviewer.get(path))[k] for k in caps)

    proof = s.upload(risk)
    receiver_id, outsider_id = str(s.people['receiver']), str(s.people['outsider'])
    shared = {'kind': 'request', 'acceptanceEvidenceRef': proof['evidenceRef']}
    step = base + '/handoff/acceptance'
    own = ok(reviewer.post(step, headers=headers(), json={**shared, 'receiverPersonId': receiver_id, 'acceptanceScope': SCOPE}))
    other_nominee = ok(reviewer.post(step, headers=headers(), json={**shared, 'receiverPersonId': outsider_id,
        'acceptanceScope': 'A different acceptance scope'}))

    counts = lambda c: [c.scalar(sa.select(sa.func.count()).select_from(table)) for table in
        (db.audit_logs, db.idempotency_keys, db.outbox_events, acceptances, resolutions)]
    with s.engine.connect() as c:
        before = counts(c)
        answer = receiver.get(path, headers={'X-CSRF-Token': ''})
        narrow = ok(answer)
        assert counts(c) == before
    assert answer.json()['meta']['operationId'] == GET
    assert narrow['audience'] == 'receiver' and narrow['status'] == 'pending'
    assert narrow['risk'] == {'riskId': str(risk.id), 'revision': 1} and 'resolution' not in narrow
    assert not any(narrow[k] for k in caps)
    assert len(narrow['acceptances']) == 1
    item = narrow['acceptances'][0]
    assert set(item) == {'acceptanceId', 'riskId', 'riskRevision', 'receiverPersonId', 'acceptanceScope',
        'acceptanceEvidenceRef', 'evidenceSha256', 'evidenceRevision', 'kind', 'createdAt', 'status', 'canConfirm', 'canHandoff'}
    assert item['acceptanceId'] == own['acceptanceId'] and item['receiverPersonId'] == receiver_id
    assert item['acceptanceScope'] == SCOPE and item['acceptanceEvidenceRef'] == proof['evidenceRef']
    assert (item['evidenceSha256'], item['evidenceRevision']) == (proof['sha256'], proof['revision'])
    assert item['kind'] == 'requested' and item['status'] == 'pending' and item['canConfirm'] and not item['canHandoff']
    assert [i['acceptanceId'] for i in ok(outsider.get(path))['acceptances']] == [other_nominee['acceptanceId']]
    rejected(receiver.get(f'risks/{other.id}/resolution'), 404, 'RESOURCE_NOT_FOUND')
    for restricted in (f'projects/{s.project}', f'projects/{s.project}/risks', f'evidence/{proof["evidenceId"]}?download=true'):
        rejected(receiver.get(restricted), 404, 'RESOURCE_NOT_FOUND')

    all_pending = ok(reviewer.get(path))
    assert all_pending['status'] == 'pending' and len(all_pending['acceptances']) == 2
    assert all(i['status'] == 'pending' and not i['canHandoff'] and not i['canConfirm'] for i in all_pending['acceptances'])
    binding = {'receiverPersonId': receiver_id, 'acceptanceEvidenceRef': proof['evidenceRef']}
    accepted = ok(receiver.post(step, headers=headers(), json={**binding, 'kind': 'confirm', 'decision': 'accept', 'acceptanceScope': SCOPE}))
    rejected(receiver.get(path), 404, 'RESOURCE_NOT_FOUND')
    live_state = ok(reviewer.get(path))
    assert live_state['status'] == 'accepted' and 'resolution' not in live_state
    confirmed, still_pending = live_state['acceptances']
    assert confirmed['acceptanceId'] == accepted['acceptanceId'] and confirmed['status'] == 'accepted' and confirmed['canHandoff']
    assert still_pending['acceptanceId'] == other_nominee['acceptanceId'] and still_pending['status'] == 'pending' and not still_pending['canHandoff']
    assert not any(i['canHandoff'] for i in ok(owner.get(path))['acceptances'])

    receipt = ok(reviewer.post(base + '/handoff', headers=headers(), json=binding))
    done = ok(reviewer.get(path))
    assert done['risk']['revision'] == 2 and done['status'] == 'resolved' and done['resolution'] == receipt
    assert not any(done[k] for k in caps) and not any(i['canConfirm'] or i['canHandoff'] for i in done['acceptances'])
    rejected(outsider.get(path), 404, 'RESOURCE_NOT_FOUND')


def test_nominee_response_contract_is_narrow_and_not_a_project_receipt(scenario):
    s = scenario
    risk = s.risk()
    proof = s.upload(risk)
    reviewer, receiver = s.clients['reviewer'], s.clients['receiver']
    path = f'risks/{risk.id}'
    item = ok(reviewer.post(path + '/handoff/acceptance', headers=headers(), json={
        'kind': 'request', 'receiverPersonId': str(s.people['receiver']), 'acceptanceEvidenceRef': proof['evidenceRef'],
        'acceptanceScope': SCOPE}))
    spec = s.api.get('/openapi.json').raise_for_status().json()
    operation = spec['paths']['/api/v1/risks/{riskId}/resolution']['get']
    schema = operation['responses']['200']['content']['application/json']['schema']
    validate = Draft202012Validator({**schema, 'components': spec['components']},
                                    format_checker=Draft202012Validator.FORMAT_CHECKER)
    member = reviewer.get(path + '/resolution').raise_for_status().json()
    nominated = receiver.get(path + '/resolution').raise_for_status().json()
    validate.validate(member)
    validate.validate(nominated)
    state = nominated['data']
    request, = state['acceptances']
    assert request['acceptanceId'] == item['acceptanceId'] and request['kind'] == 'requested'
    assert {'projectId', 'actorPersonId'}.isdisjoint(request)
    for modifications in ({'projectId': str(s.project)}, {'actorPersonId': str(s.people['reviewer'])},
                          {'canHandoff': True}, {'canConfirm': False}, {'kind': 'reviewer_verified'}, {'status': 'accepted'}):
        assert not validate.is_valid({**nominated, 'data': {**state, 'acceptances': [{**request, **modifications}]}}), modifications
    for modifications in ({'canClose': True}, {'canRequestAcceptance': True}, {'canVerifyAcceptance': True},
                          {'status': 'resolved'}, {'risk': member['data']['risk']}):
        assert not validate.is_valid({**nominated, 'data': {**state, **modifications}}), modifications
    acceptance = spec['paths']['/api/v1/risks/{riskId}/handoff/acceptance']['post']
    assert acceptance['x-actorTypes'] == ['human'] and acceptance['x-forbidAgentEvenViaHttp']


def test_resolution_read_bounds_pending_sources(scenario):
    s = scenario
    risk = s.risk()
    proof = s.upload(risk)
    reviewer = s.clients['reviewer']
    with s.engine.begin() as c:
        recipients = [uuid4() for _ in range(ACCEPTANCE_LIMIT + 1)]
        c.execute(db.persons.insert(), [dict(id=person, org_id=s.org, name='Synthetic recipient',
            created_by=s.people['owner'], is_active=True) for person in recipients])
    for person in recipients:
        ok(reviewer.post(f'risks/{risk.id}/handoff/acceptance', headers=headers(), json={
            'kind': 'request', 'receiverPersonId': str(person), 'acceptanceEvidenceRef': proof['evidenceRef'], 'acceptanceScope': SCOPE}))
    state = ok(reviewer.get(f'risks/{risk.id}/resolution'))
    assert state['status'] == 'pending' and state['acceptancesTruncated'] and len(state['acceptances']) == ACCEPTANCE_LIMIT
    assert all(i['status'] == 'pending' and not i['canHandoff'] for i in state['acceptances'])
