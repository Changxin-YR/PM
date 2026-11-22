from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from jsonschema import Draft202012Validator

from pmo import projects, schema_v1 as db
from pmo.cost_reconciliation import CREATE, Create, _read_source, _source_ref, create, current_reconciliation, grants, receipts
from pmo.identity import Browser, Failure, authenticate
from pmo.projects import fingerprint
from test_reviews import data, headers, team as team


def sheet(pid, cutoff, money='80.00', effort='12.00'):
    return {'schemaVersion': 1, 'projectId': str(pid), 'coveredThrough': cutoff.isoformat(), 'lines': [
        {'type': 'money', 'sourceRef': 'Finance GL export 44', 'netActual': money},
        {'type': 'effort', 'sourceRef': 'Time register export 31', 'netActual': effort},
    ]}


def test_source_parser_rejects_noncanonical_input():
    book = sheet(uuid4(), datetime(2024, 1, 1, 8, tzinfo=UTC))
    raw = json.dumps(book).encode()
    parsed, cutoff = _read_source(raw)
    assert parsed.lines[0].netActual == '80.00' and cutoff == datetime(2024, 1, 1, 8, tzinfo=UTC)
    for invalid in (
        raw.replace(b'"80.00"', b'80.00'), raw.replace(b'"80.00"', b'"8e1"'),
        raw.replace(b'+00:00', b''), raw.replace(b'+00:00', b'+00:99'),
        raw.replace(b'"schemaVersion": 1', b'"schemaVersion": true'),
        raw.replace(b'"schemaVersion": 1', b'"schemaVersion": 1, "schemaVersion": 1'),
        raw.replace(b'"lines":', b'"reportedTotal": 80, "lines":'),
        raw + b' ' * 16_384,
    ):
        with pytest.raises(ValueError):
            _read_source(invalid)
    assert _source_ref(' Ledger  ACK ') == _source_ref('ledger ack')
    with pytest.raises(ValueError):
        _source_ref('https://localhost/api/v1/projects/anything/costs')


def budget(c, pid, owner, kind, value, revision=1):
    t = db.cost_entries
    return c.scalar(t.insert().values(project_id=pid, created_by=owner, type=kind, category='project_budget',
        period_start='2024-01-01', period_end='2024-01-31', budget=value, actual=0,
        forecast_remaining=0, baseline_version=revision, source_ref=f'fixture:budget-v{revision}').returning(t.c.id))


def posting(c, pid, people, entry, kind, amount, *, original=None, actor='pm', pending=False):
    row = dict(project_id=pid, created_by=people[actor], cost_entry_id=entry, type=kind, source_type=kind,
        period='2024-01', amount_signed=amount, source_ref='Synthetic external line',
        occurred_at='2024-01-05T00:00:00Z', status='pending' if pending else 'verified',
        source_fingerprint=fingerprint(str(uuid4())), posting_of_id=original,
        reversal_reason='Superseded bill' if original else None)
    if not pending:
        row.update(verified_at=sa.func.clock_timestamp(), verified_by=people['sponsor'], verification_note='Checked original')
    return c.scalar(db.cost_postings.insert().values(**row).returning(db.cost_postings.c.id))


@pytest.fixture
def reconciliation(live, team, monkeypatch):
    engine, _, _, env = live
    pid, people, users, bearer = team
    monkeypatch.setenv('PMO_EVIDENCE_DIR', env['PMO_EVIDENCE_DIR'])
    monkeypatch.setenv('PMO_SYNTHETIC_POLICY_SCHEMA', env['PMO_SYNTHETIC_POLICY_SCHEMA'])
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage='delivering'))
        for person in (people['reviewer'], people['pm']):
            account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == person))
            c.execute(db.user_roles.insert().values(user_id=account, role='finance_verifier'))
            c.execute(grants.insert().values(project_id=pid, person_id=person))
        money = budget(c, pid, people['pm'], 'money', 100)
        effort = budget(c, pid, people['pm'], 'effort', 10)
        original = posting(c, pid, people, money, 'money', 100)
        posting(c, pid, people, money, 'money', -100, original=original)
        posting(c, pid, people, money, 'money', 80)
        posting(c, pid, people, effort, 'effort', 12)
        c.execute(db.cost_entries.update().where(db.cost_entries.c.id == money)
                  .values(is_current_baseline=False, revision=2))
        current_money = budget(c, pid, people['pm'], 'money', 120, revision=2)
        cutoff = c.scalar(sa.select(sa.func.clock_timestamp()))
    return engine, pid, people, users, bearer, cutoff, current_money


def evidence(users, pid, book, *, uploader='pm', verified=True, target=None):
    uploads = f'projects/{pid}/evidence'
    client = users[uploader]
    rev = data(client.get(uploads))['collectionRevision']
    kind, target_id = target or ('project', pid)
    proof = data(client.post(uploads, headers=headers(rev),
        files={'file': ('external.json', json.dumps(book).encode(), 'application/json')},
        data={'linkedResourceType': kind, 'linkedResourceId': str(target_id), 'description': 'External source snapshot'}))
    if verified:
        proof = data(users['reviewer'].post(f"evidence/{proof['evidenceId']}/verify", headers=headers(proof['revision']),
            json={'reason': 'Checked against source system', 'sha256': proof['sha256']}))
    return proof['evidenceRef']


def command(client, path, refs, *, reasons=None):
    preview = data(client.get(path))
    body = {'costHash': preview['costHash'], 'evidenceRefs': refs}
    if reasons is not None:
        body['overrunReasons'] = reasons
    return headers(preview['revision']), body


def browser(client):
    return Browser(client.cookies['pmo_session'], client.headers['X-CSRF-Token'], True)


def test_reconciliation_contract_binds_both_units_and_precise_values(reconciliation, live):
    _, pid, _, users, bearer, cutoff, _ = reconciliation
    signer = users['reviewer']
    path = f'projects/{pid}/cost-reconciliations'
    h, body = command(signer, path, [evidence(users, pid, sheet(pid, cutoff))], reasons={'effort': 'execution'})
    response = signer.post(path, headers=h, json=body)
    receipt = data(response)
    current = signer.get(path)
    spec = live[1].get('/openapi.json').raise_for_status().json()
    route = spec['paths']['/api/v1/projects/{projectId}/cost-reconciliations']
    money, effort = receipt['lines']
    bad_lines = ([money], [money, money], [effort, money], [{**money, 'unit': 'person_day'}, effort],
                 [{**money, 'actual': '8e1'}, effort], [{**money, 'actual': 80}, effort],
                 [{**money, 'initial': {**money['initial'], 'rate': 'NaN'}}, effort],
                 [{**money, 'proof': {**money['proof'], 'verifiedAt': 'not a timestamp'}}, effort])
    for method, actual in [('get', current), ('post', response)]:
        schema = route[method]['responses']['200']['content']['application/json']['schema']
        validate = Draft202012Validator({**schema, 'components': spec['components']},
                                        format_checker=Draft202012Validator.FORMAT_CHECKER)
        wire = actual.json()
        validate.validate(wire)
        for bad in bad_lines:
            invalid = {**receipt, 'lines': bad}
            edited = {**wire['data'], 'current': invalid} if method == 'get' else invalid
            assert not validate.is_valid({**wire, 'data': edited}), bad
        assert route[method]['x-actorTypes'] == ['human'] and route[method]['x-forbidAgentEvenViaHttp']
    assert data(current)['current'] == receipt
    assert live[1].get('/api/v1/' + path, headers=bearer).status_code == 403
    assert live[1].post('/api/v1/' + path, headers={**h, **bearer}, json=body).status_code == 403


def test_receipt_retains_two_bases_and_becomes_stale(reconciliation, live, monkeypatch):
    engine, pid, _, users, _, cutoff, _ = reconciliation
    signer, pm = users['reviewer'], users['pm']
    path = f'projects/{pid}/cost-reconciliations'
    book = sheet(pid, cutoff)
    ref = evidence(users, pid, book)
    h, body = command(signer, path, [ref], reasons={'effort': 'execution'})
    assert data(signer.get(path))['canCreate']
    saved = signer.post(path, headers=h, json=body)
    receipt = data(saved)
    assert signer.post(path, headers=h, json=body).json() == saved.json()
    assert receipt['policyStatus'] == 'POLICY_PENDING' and receipt['revision'] == 2
    money, effort = receipt['lines']
    assert (money['actual'], money['sourceNetActual'], money['difference']) == ('80.00', '80.00', '0.00')
    assert (money['initial']['budget'], money['initial']['difference']) == ('100.00', '-20.00')
    assert (money['current']['budget'], money['current']['difference']) == ('120.00', '-40.00')
    assert money['initial']['sourceRefs'][0].startswith('cost-baseline-history:')
    assert money['current']['sourceRefs'][0].startswith('cost-entry:')
    assert (effort['actual'], effort['current']['rate'], effort['overrunReason']) == ('12.00', '0.20000000', 'execution')
    assert data(pm.get(path))['current'] == receipt and not data(pm.get(path))['canCreate']
    with engine.begin() as c:
        row = c.execute(sa.select(receipts).where(receipts.c.project_id == pid)).mappings().one()
        assert fingerprint(row.basis) == receipt['costHash']
        assert row.receipt_hash == receipt['receiptHash'] and row.request_id == UUID(saved.headers['X-Request-ID'])
        for table, table_id, source in ((db.audit_logs, db.audit_logs.c.request_id, CREATE),
                                        (db.outbox_events, db.outbox_events.c.dedupe_key, 'cost.reconciled')):
            column = table.c.action if table is db.audit_logs else table.c.event_type
            assert c.scalar(sa.select(sa.func.count()).select_from(table).where(table_id == str(row.request_id), column == source)) == 1
        p = c.execute(sa.select(db.projects).where(db.projects.c.id == pid).with_for_update(read=True)).mappings().one()
        assert current_reconciliation(c, authenticate(c, browser(pm)), p)[1][0] == f"cost-reconciliation:{row.id}@2"
    with pytest.raises(sa.exc.DBAPIError), engine.begin() as c:
        c.execute(receipts.delete().where(receipts.c.project_id == pid))
    with engine.begin() as c:
        c.execute(db.projects.update().where(db.projects.c.id == pid).values(name='Unrelated label', revision=10))
        key = c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id == UUID(ref[9:])))
    assert data(pm.get(path))['current'] == receipt
    original = Path(live[3]['PMO_EVIDENCE_DIR']) / key
    payload = original.read_bytes()
    try:
        original.write_bytes(payload.replace(b'80.00', b'81.00'))
        assert data(pm.get(path))['current'] is None
    finally:
        original.write_bytes(payload)
    assert data(pm.get(path))['current'] == receipt

    monkeypatch.setattr(projects, 'engine', lambda: engine)
    monkeypatch.setenv('PMO_SYNTHETIC_POLICY_SCHEMA', 'pmo_test_' + uuid4().hex)
    with pytest.raises(Failure, match='POLICY_PENDING'):
        create(UUID(pid), Create.model_validate(body), browser(signer), uuid4().hex, '2')

    data(pm.put(f'projects/{pid}/cost-forecasts', headers=headers(), json={
        'type': 'money', 'forecastRemaining': '0', 'reason': 'Reaffirmed forecast'}))
    stale = data(signer.get(path))
    assert stale['current'] is None and stale['revision'] == 2 and stale['costHash'] != body['costHash']
    assert signer.post(path, headers=headers(2), json=body).json()['code'] == 'RECONCILIATION_STALE'
    newer_h, newer = command(signer, path, [ref], reasons={'effort': 'execution'})
    replacement = data(signer.post(path, headers=newer_h, json=newer))
    assert replacement['revision'] == 3
    with engine.begin() as c:
        c.execute(grants.update().where(grants.c.id == UUID(replacement['grantId'])).values(revoked_at=sa.func.now()))
    assert signer.post(path, headers=newer_h, json=newer).status_code == 403
    assert data(pm.get(path))['current'] is None


def test_requires_independent_sources_and_parties(reconciliation, live):
    engine, pid, people, users, bearer, cutoff, entry = reconciliation
    signer, pm, outsider = users['reviewer'], users['pm'], users['outsider']
    path = f'projects/{pid}/cost-reconciliations'
    earlier = evidence(users, pid, sheet(pid, cutoff - timedelta(days=1)))
    valid = evidence(users, pid, sheet(pid, cutoff))
    wrong = evidence(users, pid, sheet(pid, cutoff, effort='12.01'))
    pending = evidence(users, pid, sheet(pid, cutoff), verified=False)
    foreign = evidence(users, pid, sheet(uuid4(), cutoff))
    repeated = sheet(pid, cutoff)
    repeated['lines'][1]['sourceRef'] = 'FINANCE GL  export 44'
    reused = evidence(users, pid, repeated)
    for ref, code in ((earlier, 'RECONCILIATION_SOURCE_COVERAGE'), (foreign, 'RECONCILIATION_SOURCE_COVERAGE'),
                      (reused, 'RECONCILIATION_SOURCE_COVERAGE'), (wrong, 'RECONCILIATION_TOTAL_MISMATCH'),
                      (pending, 'RECONCILIATION_EVIDENCE_UNTRUSTED')):
        h, body = command(signer, path, [ref], reasons={'effort': 'estimate'})
        rejected = signer.post(path, headers=h, json=body)
        assert rejected.status_code == 422 and rejected.json()['code'] == code, rejected.text
    h, body = command(signer, path, [valid])
    assert signer.post(path, headers=h, json=body).json()['code'] == 'RECONCILIATION_OVERRUN_REASON_REQUIRED'
    assert signer.post(path, headers=h, json={**body, 'money': '80.00'}).json()['code'] == 'VALIDATION_FAILED'
    assert pm.post(path, headers=h, json=body).status_code == 403  # even with finance and grant
    assert outsider.get(path).status_code == 404
    assert live[1].post('/api/v1/' + path, headers={**h, **bearer}, json=body).status_code == 403
    with engine.begin() as c:
        posting(c, pid, people, entry, 'money', 1, actor='reviewer')
    h, body = command(signer, path, [valid], reasons={'effort': 'estimate'})
    denied = signer.post(path, headers=h, json=body)
    assert denied.status_code == 403 and denied.json()['code'] == 'INDEPENDENT_RECONCILER_REQUIRED'
    with engine.begin() as c:
        assert not c.scalar(sa.select(receipts.c.id).where(receipts.c.project_id == pid))


def test_concurrent_create_needs_one_revision_and_both_sources(reconciliation):
    engine, pid, people, users, _, cutoff, entry = reconciliation
    path = f'projects/{pid}/cost-reconciliations'
    signer = users['reviewer']
    book = sheet(pid, cutoff)
    money = evidence(users, pid, {**book, 'lines': book['lines'][:1]})
    effort = evidence(users, pid, {**book, 'lines': book['lines'][1:]})
    h, body = command(signer, path, [money], reasons={'effort': 'scope'})
    assert signer.post(path, headers=h, json=body).json()['code'] == 'RECONCILIATION_SOURCE_COVERAGE'
    body['evidenceRefs'] = [effort, money]
    with engine.begin() as c:
        c.exec_driver_sql('''CREATE FUNCTION fail_reconciliation_outbox() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.event_type='cost.reconciled' THEN RAISE EXCEPTION 'Synthetic failure'; END IF; RETURN NEW; END $$''')
        c.exec_driver_sql('''CREATE TRIGGER fail_reconciliation_outbox BEFORE INSERT ON outbox_events
            FOR EACH ROW EXECUTE FUNCTION fail_reconciliation_outbox()''')
    try:
        assert signer.post(path, headers=h, json=body).status_code == 500
        with engine.begin() as c:
            assert not c.scalar(sa.select(receipts.c.id).where(receipts.c.project_id == pid))
            assert not c.scalar(sa.select(db.audit_logs.c.id).where(db.audit_logs.c.project_id == pid,
                db.audit_logs.c.action == CREATE, db.audit_logs.c.result == 'success'))
            assert not c.scalar(sa.select(db.idempotency_keys.c.id).where(db.idempotency_keys.c.operation == CREATE,
                db.idempotency_keys.c.idempotency_key == h['Idempotency-Key']))
    finally:
        with engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER fail_reconciliation_outbox ON outbox_events')
            c.exec_driver_sql('DROP FUNCTION fail_reconciliation_outbox()')
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: signer.post(path, headers=headers(), json=body), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert next(r for r in responses if r.status_code == 409).json()['code'] == 'VERSION_CONFLICT'
    with engine.begin() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(receipts).where(receipts.c.project_id == pid)) == 1
        posting(c, pid, people, entry, 'money', 1, pending=True)
    h, changed = command(signer, path, [money, effort], reasons={'effort': 'scope'})
    assert data(signer.get(path))['current'] is None
    assert signer.post(path, headers=h, json=changed).json()['code'] == 'UNVERIFIED_OR_FUTURE_POSTINGS'
