from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from textwrap import wrap
from types import SimpleNamespace
from uuid import UUID, uuid4

from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest
import sqlalchemy as sa

from pmo import projects, requirement_exceptions as exceptions, review_outcomes as outcomes, schema_v1 as db
from pmo.evidence import links
from pmo.identity import Browser, Failure, authenticate
from pmo.scope_quality import metrics
from test_evidence import binary
from test_lifecycle import close_materials, complete_milestones, execute, execution_fixture, gate, propose, stage
from test_reviews import bench, data, decide, headers, outcome, result_counts, team as team


TEST_ID = UUID('10000000-0000-4000-8000-000000000009')
TEST_EXPIRY = datetime(2026, 12, 1, 12, tzinfo=UTC)


def pdf_text(text):
    writer = PdfWriter()
    name = NameObject
    lines = [line for paragraph in text.splitlines()
             for line in wrap(paragraph, 100, break_long_words=False, break_on_hyphens=False)]
    for start in range(0, len(lines), 42):
        page = writer.add_blank_page(640, 680)
        page[name('/Resources')] = DictionaryObject({name('/Font'): DictionaryObject({name('/F1'): DictionaryObject({
            name('/Type'): name('/Font'), name('/Subtype'): name('/Type1'), name('/BaseFont'): name('/Courier'),
        })})})
        stream = DecodedStreamObject()
        body = '\n'.join('(' + line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj T*'
                         for line in lines[start:start + 42])
        stream.set_data(('BT /F1 9 Tf 36 640 Td 14 TL\n' + body + '\nET').encode('ascii'))
        page[name('/Contents')] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_binds_customer_current_versions_expiry_and_deviations():
    scope = exceptions.AcceptanceScope(decision='accept', gapHash='0' * 64, expiresAt=TEST_EXPIRY, items=[
        exceptions.AcceptedItem(requirementId=TEST_ID, revision=2, baselineVersion=1,
                                requirementHash='1' * 64, deviation='The approved synthetic export omits region (v1).')])
    text = exceptions.written_acceptance(TEST_ID, TEST_ID, scope)
    assert text.startswith('PMO DEC-09 SYNTHETIC REQUIREMENT EXCEPTION\n')
    assert '2026-12-01T12:00:00.000000Z' in text
    original = pdf_text(text)
    assert exceptions._matches_pdf(original, text)
    for changed in (text.replace('revision 2', 'revision 1'), text.replace('baseline 1', 'baseline 2'),
                    text.replace('region', 'area'), text.replace('12:00:00', '13:00:00'),
                    exceptions.written_acceptance(TEST_ID, uuid4(), scope), 'I do not accept.\n' + text):
        assert not exceptions._matches_pdf(original, changed)
    assert not exceptions._matches_pdf(binary('png'), text)


def test_sign_dto_rejects_broad_waivers_and_ambiguous_scope():
    item = dict(requirementId=str(TEST_ID), revision=2, baselineVersion=1, requirementHash='1' * 64,
                deviation='The synthetic export omits the region.')
    body = dict(decision='accept', gapHash='0' * 64, items=[item], expiresAt=TEST_EXPIRY.isoformat(),
                evidenceRef=f'evidence:{TEST_ID}')
    assert exceptions.Sign.model_validate(body).items[0].revision == 2
    for override in ({'decision': True}, {'signedException': True}, {'waiveBudget': True}, {'items': [item, item]},
                     {'items': [{**item, 'revision': True}]}, {'items': [{**item, 'deviation': '   '}]},
                     {'items': [{**item, 'deviation': 'x' * 1001}]}, {'expiresAt': '2026-12-01T12:00:00'},
                     {'expiresAt': '0001-01-01T00:00:00+01:00'}):
        with pytest.raises(ValidationError):
            exceptions.Sign.model_validate({**body, **override})


def browser(client, *, writing=True):
    return Browser(client.cookies['pmo_session'], client.headers['X-CSRF-Token'], writing)


def denied(status, code, method, *args):
    with pytest.raises(Failure) as exc:
        method(*args)
    assert (exc.value.status, exc.value.code) == (status, code)


@pytest.fixture
def case(live, team, monkeypatch):
    engine, _, _, env = live
    pid, people, clients, bearer = team
    pid = UUID(pid)
    path = f'projects/{pid}'
    monkeypatch.setenv('PMO_SYNTHETIC_POLICY_SCHEMA', env['PMO_SYNTHETIC_POLICY_SCHEMA'])
    monkeypatch.setenv('PMO_EVIDENCE_DIR', env['PMO_EVIDENCE_DIR'])
    monkeypatch.setattr(projects, 'engine', lambda: engine)
    customer, pm, reviewer = [clients[role] for role in ('client_representative', 'pm', 'reviewer')]

    def stage(value):
        with engine.begin() as c:
            p = db.projects
            c.execute(p.update().where(p.c.id == pid).values(stage=value, revision=p.c.revision + 1))

    stage('designing')
    for priority, desc in [('P0', 'Export audit records'), ('P2', 'Display delivery region')]:
        data(pm.post(path + '/requirements', headers=headers(), json={'desc': desc, 'acceptanceCriteria': 'Matches source',
            'priority': priority, 'plannedConfirmAt': '2026-12-01'}))
    baseline_path = path + '/requirement-baseline'
    for role, client in (('pm', pm), ('client_representative', customer)):
        baseline = data(client.get(baseline_path))
        data(client.post(baseline_path + '/sign', headers=headers(baseline['revision']), json={
            'role': role, 'contentHash': baseline['contentHash'], 'reason': 'Agreed isolated test scope.'}))
    stage('delivering')
    risk = data(pm.post(path + '/risks', headers=headers(), json={'statement': 'An open blocker that DEC-09 cannot waive',
        'response': 'Use independent risk workflow', 'prob': 2, 'impact': 3, 'isBlocking': True}))

    def state(client=customer):
        return exceptions.get(pid, browser(client, writing=False)).data

    def scope(current=None):
        if current is None:
            current = state()
        items = [exceptions.AcceptedItem(**g.model_dump(include=set(exceptions.RequirementVersion.model_fields)),
                 deviation=f'Explicit synthetic deviation for {g.priority}: one report field is missing.') for g in current.gaps]
        return exceptions.AcceptanceScope(decision='accept', gapHash=current.gapHash, items=items,
            expiresAt=datetime.now(UTC).replace(microsecond=0) + timedelta(days=2))

    def upload(target, raw, *, verified=True, is_pdf=True):
        revision = data(pm.get(path + '/evidence'))['collectionRevision']
        file = ('exception.pdf', raw, 'application/pdf') if is_pdf else ('original.png', raw, 'image/png')
        proof = data(pm.post(path + '/evidence', headers=headers(revision), files={'file': file},
            data={'linkedResourceType': 'requirement', 'linkedResourceId': str(target)}))
        if verified:
            proof = data(reviewer.post(f"evidence/{proof['evidenceId']}/verify", headers=headers(proof['revision']),
                json={'reason': 'Reviewed the synthetic private original.', 'sha256': proof['sha256']}))
        return proof

    person = people['client_representative']

    def written(scope):
        return exceptions.written_acceptance(pid, person, scope)

    def command(scope, *, text=None, verified=True):
        proof = upload(scope.items[0].requirementId, pdf_text(written(scope) if text is None else text), verified=verified)
        return exceptions.Sign(**scope.model_dump(), evidenceRef=proof['evidenceRef'])

    def sign(command, *, key=None, revision=1, who=customer):
        return exceptions.sign(pid, command, browser(who), key or uuid4().hex, str(revision))

    def applicable():
        with engine.begin() as c:
            a = authenticate(c, browser(pm, writing=False))
            p = c.execute(sa.select(db.projects).where(db.projects.c.id == pid).with_for_update(read=True)).mappings().one()
            return exceptions.gate_check(c, a, p)

    return SimpleNamespace(engine=engine, pid=pid, path=path, people=people, customer=customer, pm=pm, reviewer=reviewer,
        outsider=clients['outsider'], bearer=bearer['Authorization'], env=env, stage=stage, risk=risk, state=state, scope=scope,
        written=written, upload=upload, command=command, sign=sign, applicable=applicable)


def test_customer_signs_verified_original_and_revokes_under_review(case, monkeypatch):
    x = case
    state = x.state()
    assert state.canSign and not state.canRevoke and state.revision == 1 and len(state.gaps) == 2
    assert state.policyStatus == 'POLICY_PENDING' and state.signerPersonId == x.people['client_representative']
    assert not x.state(x.pm).canSign
    denied(404, 'RESOURCE_NOT_FOUND', x.state, x.outsider)
    denied(403, 'FORBIDDEN', exceptions.get, x.pid, x.bearer)

    scope = x.scope(state)
    command = x.command(scope)
    for who in (x.pm, x.reviewer):
        denied(403, 'INDEPENDENT_CUSTOMER_REQUIRED', lambda: x.sign(command, who=who))
    denied(403, 'FORBIDDEN', exceptions.sign, x.pid, command, x.bearer, uuid4().hex, '1')
    denied(404, 'RESOURCE_NOT_FOUND', exceptions.sign, uuid4(), command, browser(x.customer), uuid4().hex, '1')
    denied(409, 'VERSION_CONFLICT', lambda: x.sign(command, revision=2))
    assert x.applicable() is None

    key = uuid4().hex
    first = x.sign(command, key=key)
    saved = first.data
    assert saved.policyStatus == 'POLICY_PENDING' and saved.signedBy == x.people['client_representative']
    assert x.sign(command, key=key) == first
    denied(409, 'IDEMPOTENCY_CONFLICT', lambda: x.sign(command.model_copy(update={
        'expiresAt': command.expiresAt + timedelta(days=1)}), key=key))
    read = x.state()
    assert read.current == saved and read.canRevoke and read.revocableExceptionId == saved.exceptionId and not read.canSign
    proof = x.applicable()
    assert proof is not None and proof.snapshot == saved.model_dump(mode='json')
    assert saved.evidenceRef in proof.source_refs and len(proof.source_refs) == 4
    with x.engine.connect() as c:
        q, _ = metrics(c, x.pid)
        assert q.confirmedP0 == q.confirmedWeight == 0
    missing = {gap['code'] for gap in data(x.pm.get(x.path + '/closing/check'))['missing']}
    assert {'BLOCKING_RISK_UNRESOLVED', 'COST_RECONCILIATION_REQUIRED'} <= missing

    with monkeypatch.context() as expired:
        expired.setattr(exceptions, '_now', lambda c: saved.expiresAt)
        assert x.applicable() is None and x.state().current is None
    x.stage('closing_review')
    assert x.applicable() == proof
    rkey = uuid4().hex
    revoke = exceptions.Revoke(reason='Withdraw the written synthetic acceptance.')
    revoked = exceptions.revoke(x.pid, saved.exceptionId, revoke, browser(x.customer), rkey, '2')
    assert revoked.data.revision == 3 and revoked.data.exceptionId == saved.exceptionId
    assert exceptions.revoke(x.pid, saved.exceptionId, revoke, browser(x.customer), rkey, '2') == revoked
    assert x.applicable() is None and not x.state().canRevoke and x.state().revision == 3
    with x.engine.connect() as c:
        t = exceptions.events.c
        history = c.execute(sa.select(t.kind, t.revokes_id, t.request_id).where(t.project_id == x.pid).order_by(t.revision)).all()
        assert [(row.kind, row.revokes_id) for row in history] == [('signed', None), ('revoked', saved.exceptionId)]
        audit = db.audit_logs.c
        events = db.outbox_events.c
        for row in history:
            assert c.scalar(sa.select(audit.result).where(audit.request_id == str(row.request_id))) == 'success'
            assert c.scalar(sa.select(events.event_type).where(events.dedupe_key == str(row.request_id))) == f'requirement_exception.{row.kind}'
    x.stage('archived')
    assert not x.state().canSign and not x.state().canRevoke and x.applicable() is None
    denied(422, 'REQUIREMENT_EXCEPTION_LOCKED', lambda: x.sign(command, revision=3))
    denied(422, 'REQUIREMENT_EXCEPTION_LOCKED', exceptions.revoke, x.pid, saved.exceptionId, revoke,
           browser(x.customer), uuid4().hex, '3')


def test_requires_every_current_gap_and_bytes_in_exact_scope(case):
    x = case
    scope = x.scope()
    command = x.command(scope)
    denied(422, 'REQUIREMENT_EXCEPTION_SCOPE_INCOMPLETE', x.sign, command.model_copy(update={'items': command.items[:1]}))
    stale_item = command.items[0].model_copy(update={'revision': command.items[0].revision - 1})
    denied(422, 'REQUIREMENT_EXCEPTION_SCOPE_INCOMPLETE', x.sign,
           command.model_copy(update={'items': [stale_item, command.items[1]]}))
    denied(409, 'REQUIREMENT_EXCEPTION_SCOPE_STALE', x.sign, command.model_copy(update={'gapHash': '0' * 64}))
    denied(422, 'INVALID_REQUIREMENT_EXCEPTION_EXPIRY', x.sign,
           command.model_copy(update={'expiresAt': datetime.now(UTC) - timedelta(minutes=1)}))
    denied(422, 'REQUIREMENT_EXCEPTION_EVIDENCE_REQUIRED', x.sign, x.command(scope, verified=False))
    denied(422, 'REQUIREMENT_EXCEPTION_SCOPE_NOT_IN_ORIGINAL', x.sign,
           x.command(scope, text=x.written(scope).replace('one report field', 'two report fields')))
    denied(422, 'REQUIREMENT_EXCEPTION_SCOPE_NOT_IN_ORIGINAL', x.sign,
           command.model_copy(update={'expiresAt': command.expiresAt + timedelta(days=1)}))
    denied(404, 'RESOURCE_NOT_FOUND', x.sign, command.model_copy(update={'evidenceRef': f'evidence:{uuid4()}'}))
    # A real attachment for the wrong resource cannot become a scope document, even with identical bytes.
    rev = data(x.pm.get(x.path + '/evidence'))['collectionRevision']
    other = data(x.pm.post(x.path + '/evidence', headers=headers(rev),
        files={'file': ('exception.pdf', pdf_text(x.written(scope)), 'application/pdf')},
        data={'linkedResourceType': 'risk', 'linkedResourceId': x.risk['riskId']}))
    denied(422, 'EVIDENCE_TARGET_MISMATCH', x.sign, command.model_copy(update={'evidenceRef': other['evidenceRef']}))
    assert x.state().revision == 1 and x.applicable() is None


def test_invalidations_after_signing_and_production_policy(case, monkeypatch):
    x = case
    scope = x.scope()
    command = x.command(scope)
    saved = x.sign(command).data
    assert x.applicable() is not None

    eid = UUID(saved.evidenceRef.removeprefix('evidence:'))
    with x.engine.connect() as c:
        key = c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id == eid))
    original = Path(x.env['PMO_EVIDENCE_DIR']) / key
    raw = original.read_bytes()
    try:
        original.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        assert x.applicable() is None and x.state().current is None
    finally:
        original.write_bytes(raw)
    with x.engine.begin() as c:
        c.execute(links.update().where(links.c.evidence_id == eid).values(revision=saved.evidenceRevision + 1))
    assert x.applicable() is None
    with x.engine.begin() as c:
        c.execute(links.update().where(links.c.evidence_id == eid).values(revision=saved.evidenceRevision))
    assert x.applicable() is not None

    with monkeypatch.context() as prod:
        prod.setenv('PMO_SYNTHETIC_POLICY_SCHEMA', 'public')
        state = x.state()
        assert state.policyStatus == 'POLICY_PENDING' and state.current is None and not state.canSign
        assert x.applicable() is None
        denied(403, 'POLICY_PENDING', lambda: x.sign(command, revision=2))

    target = scope.items[0]
    deliver = x.upload(target.requirementId, binary('png'), is_pdf=False)
    data(x.pm.post(f'requirements/{target.requirementId}/deliver', headers=headers(target.revision),
                   json={'evidenceRef': deliver['evidenceRef']}))
    assert x.state().current is None and x.applicable() is None
    denied(409, 'REQUIREMENT_EXCEPTION_SCOPE_STALE', lambda: x.sign(command, revision=2))
    replacement = x.sign(x.command(x.scope()), revision=2).data
    assert replacement.revision == 3 and x.applicable() is not None

    with x.engine.begin() as c:
        t = db.project_members
        c.execute(t.update().where(t.c.project_id == x.pid, t.c.person_id == x.people['client_representative'],
            t.c.role == 'client_representative').values(assignment_status='revoked', revoked_at=sa.func.now()))
    assert x.applicable() is None and x.state(x.pm).current is None
    denied(404, 'RESOURCE_NOT_FOUND', x.state)


def test_one_concurrent_sign_and_outbox(case):
    x = case
    command = x.command(x.scope())

    def submit(_):
        try:
            return x.sign(command).data.revision
        except Failure as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(submit, range(2))) == {2, 'VERSION_CONFLICT'}
    with x.engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(exceptions.events).where(exceptions.events.c.project_id == x.pid)) == 1
        assert c.scalar(sa.select(sa.func.count()).select_from(db.outbox_events).where(
            db.outbox_events.c.event_type == 'requirement_exception.signed',
            db.outbox_events.c.payload['projectId'].astext == str(x.pid))) == 1


DEVIATION = 'In the synthetic retention export, this release omits the named report field.'


def deliver_with_exception_material(live, team, *, close_risk=True, **kwargs):
    pid, people, users, _ = team
    pm, reviewer, customer, accountant = (users[k] for k in ('pm', 'reviewer', 'client_representative', 'outsider'))
    project, requirement, risk = execution_fixture(live, team)
    complete_milestones(pm, reviewer, project)
    data(execute(pm, project, data(propose(pm, project, 'delivering'))))
    _, risk_proof, cost_proof = close_materials(pm, reviewer, customer, project, requirement, risk, confirm=False, **kwargs)
    if close_risk:
        data(reviewer.post(f'risks/{risk}/close', headers=headers(), json={'resolutionEvidenceRef': risk_proof['evidenceRef']}))
    reco = project + '/cost-reconciliations'
    ledger = data(accountant.get(reco))
    reconciliation = {'costHash': ledger['costHash'], 'evidenceRefs': [cost_proof['evidenceRef']]}
    if 'actuals' in kwargs:
        reconciliation['overrunReasons'] = {'money': 'estimate', 'effort': 'execution'}
    data(accountant.post(reco, headers=headers(ledger['revision']), json=reconciliation))

    def prepare():
        state = data(customer.get(project + '/requirement-exceptions'))
        assert state['canSign'] and len(state['gaps']) == 1
        scope = exceptions.AcceptanceScope(decision='accept', gapHash=state['gapHash'],
            expiresAt=(datetime.now(UTC) + timedelta(days=1)).replace(second=0, microsecond=0),
            items=[exceptions.AcceptedItem(**{k: state['gaps'][0][k] for k in exceptions.RequirementVersion.model_fields},
                                           deviation=DEVIATION)])
        original = exceptions.written_acceptance(UUID(pid), people['client_representative'], scope)
        rev = data(pm.get(project + '/evidence'))['collectionRevision']
        pdf = data(pm.post(project + '/evidence', headers=headers(rev),
            files={'file': ('signed-scope.pdf', pdf_text(original), 'application/pdf')},
            data={'linkedResourceType': 'requirement', 'linkedResourceId': requirement}))
        proof = data(reviewer.post(f"evidence/{pdf['evidenceId']}/verify", headers=headers(pdf['revision']),
            json={'reason': 'Checked original synthetic acceptance and its exact intended scope', 'sha256': pdf['sha256']}))
        return state, dict(scope.model_dump(mode='json'), evidenceRef=proof['evidenceRef'])

    return project, prepare


def test_conditional_closing_freezes_revalidates_and_records_exact_scope(live, team, monkeypatch):
    engine, api, _, env = live
    pid, people, users, agent = team
    pm, reviewer, customer = (users[k] for k in ('pm', 'reviewer', 'client_representative'))
    project, prepare = deliver_with_exception_material(live, team)
    path = project + '/requirement-exceptions'
    check = project + '/closing/check'
    assert {r['code'] for r in data(pm.get(check))['missing']} == {'CUSTOMER_ACCEPTANCE_REQUIRED'}
    state, command = prepare()
    assert api.post('/api/v1/' + path, headers={**agent, **headers(state['revision'])}, json=command).status_code == 403
    assert pm.post(path, headers=headers(state['revision']), json=command).json()['code'] == 'INDEPENDENT_CUSTOMER_REQUIRED'
    assert users['outsider'].post(path, headers=headers(state['revision']), json=command).json()['code'] == 'INDEPENDENT_CUSTOMER_REQUIRED'
    sign_headers = headers(state['revision'])
    signed = data(customer.post(path, headers=sign_headers, json=command))
    assert signed['signedBy'] == str(people['client_representative'])
    assert data(customer.post(path, headers=sign_headers, json=command)) == signed
    assert data(pm.get(check))['eligible']
    review = gate(pm, project, 'closing')
    frozen = bench(reviewer, review)
    assert frozen['requirementException'] == signed
    assert decide(reviewer, review).json()['code'] == 'CLOSING_CONDITIONAL_DECISION_REQUIRED'
    assert decide(pm, review, verdict='conditional_pass').status_code == 403
    decision_headers = headers(frozen['review']['revision'])
    decision_command = outcome(frozen, verdict='conditional_pass')
    assert api.post('/api/v1/' + review + '/decisions', headers={**agent, **decision_headers}, json=decision_command).status_code == 403

    # Exercise the very same decision entry point with time advanced, without sleeping or editing the receipt.
    with monkeypatch.context() as clock:
        clock.setenv('PMO_SYNTHETIC_POLICY_SCHEMA', env['PMO_SYNTHETIC_POLICY_SCHEMA'])
        clock.setenv('PMO_EVIDENCE_DIR', env['PMO_EVIDENCE_DIR'])
        clock.setattr(projects, 'engine', lambda: engine)
        clock.setattr(exceptions, '_now', lambda c: datetime.fromisoformat(signed['expiresAt']))
        denied(409, 'REVIEW_SNAPSHOT_STALE', outcomes.write_action, browser(reviewer), outcomes.DECIDE,
               UUID(frozen['review']['reviewId']), outcomes.Decide.model_validate(decision_command), uuid4().hex, frozen['review']['revision'])

    # The original bytes must still match their server-side hash at final decision time.
    with engine.connect() as c:
        eid = UUID(signed['evidenceRef'].removeprefix('evidence:'))
        key = c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id == eid))
    original = Path(env['PMO_EVIDENCE_DIR']) / key
    contents = original.read_bytes()
    try:
        original.write_bytes(contents[:-1] + bytes([contents[-1] ^ 1]))
        assert decide(reviewer, review, verdict='conditional_pass').json()['code'] == 'REVIEW_SNAPSHOT_STALE'
    finally:
        original.write_bytes(contents)

    data(customer.post(path + f"/{signed['exceptionId']}/revoke", headers=headers(signed['revision']),
        json={'reason': 'Customer withdraws acceptance before decision.'}))
    assert decide(reviewer, review, verdict='conditional_pass').json()['code'] == 'REVIEW_SNAPSHOT_STALE'
    assert stage(pm, project)['stage'] == 'closing_review'
    data(pm.post(review + '/withdraw', headers=headers(frozen['review']['revision']), json={'reason': 'Replace revoked material.'}))
    state, command = prepare()
    signed = data(customer.post(path, headers=headers(state['revision']), json=command))
    review = gate(pm, project, 'closing')
    frozen = bench(reviewer, review)
    decision_headers = headers(frozen['review']['revision'])
    decision_command = outcome(frozen, verdict='conditional_pass')
    with engine.connect() as c:
        before = result_counts(c, pid)
    result = data(reviewer.post(review + '/decisions', headers=decision_headers, json=decision_command))
    assert result['decision']['stage'] == 'closed' and result['requirementException'] == signed
    assert data(reviewer.post(review + '/decisions', headers=decision_headers, json=decision_command)) == result
    assert bench(reviewer, review)['requirementException'] == signed
    with engine.connect() as c:
        assert result_counts(c, pid) == ('closed', *(n + 1 for n in before[1:-1]), before[-1])
        row = c.execute(sa.select(outcomes.outcomes).where(outcomes.outcomes.c.review_id == UUID(frozen['review']['reviewId']))).mappings().one()
        assert row.payload['requirementException'] == signed and row.payload['materialHash'] == row.material_hash
        assert row.payload_hash == projects.fingerprint(row.payload)
        assert metrics(c, UUID(pid))[0].confirmedP0 == 0


def test_valid_exception_does_not_waive_overruns_commitments_or_risk(live, team):
    _, _, users, _ = team
    pm, customer = (users[k] for k in ('pm', 'client_representative'))
    project, prepare = deliver_with_exception_material(live, team, close_risk=False,
        actuals=('125.00', '13.00'), remaining=('5.00', '1.00'))
    state, command = prepare()
    data(customer.post(project + '/requirement-exceptions', headers=headers(state['revision']), json=command))
    check = data(pm.get(project + '/closing/check'))
    assert not check['eligible']
    assert {r['code'] for r in check['missing']} == {'MONEY_CLOSING_VARIANCE', 'EFFORT_CLOSING_VARIANCE',
        'MONEY_FORECAST_NOT_ZERO', 'EFFORT_FORECAST_NOT_ZERO', 'BLOCKING_RISK_UNRESOLVED'}
    response = pm.post(project + '/reviews', headers=headers(stage(pm, project)['revision']), json={'reviewType': 'closing'})
    assert response.status_code == 422 and response.json()['code'] == 'GATE_REQUIREMENTS_NOT_MET'
    assert stage(pm, project)['stage'] == 'delivering'
