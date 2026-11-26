"""Exercise the policy registry without granting a real organization approval."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import Client
from test_database_roles import serve
from test_lifecycle import begin_execution, judgment, propose, stage
from test_reviews import data, headers
from test_reviews import team as team  # noqa: PLC0414

from pmo import policy
from pmo import policy_admin as admin
from pmo import schema_v1 as db
from pmo.identity import Failure

TARGET = 'synthetic-policy-drill'
ORIGINAL = b'isolated acceptance fixture, never a business signature'


def fixture_authorities(engine, p, people, code):
    registrar = people['outsider']
    expires = datetime.now(UTC) + timedelta(hours=1)
    with engine.begin() as c:
        account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == registrar))
        if not c.scalar(sa.select(db.user_roles.c.id).where(db.user_roles.c.user_id == account,
                                                           db.user_roles.c.role == 'system_admin')):
            c.execute(db.user_roles.insert().values(user_id=account, role='system_admin'))
        return {
            capability: admin.grant(c, p.org_id, registrar, people[who], capability, scope,
                                    f'fixture-{capability}-authority', ORIGINAL, expires)
            for capability, who, scope in (
                ('approve', 'sponsor', code), ('verify', 'pm', code), ('activate', 'reviewer', TARGET))
        }


def draft(p, people, actions, code, **options):
    stamp = datetime.now(UTC)
    values = {'orgId': p.org_id, 'projectId': p.id, 'environment': TARGET, 'decisionCode': code,
        'version': 'fixture-' + uuid4().hex, 'actions': actions,
        'policyHash': policy.canonical_hash(policy.payload(code, actions)), 'evidenceRef': 'fixture-approval-original',
        'evidenceHash': sha256(ORIGINAL).hexdigest(), 'approverId': people['sponsor'], 'approvedAt': stamp,
        'effectiveFrom': stamp, 'effectiveUntil': stamp + timedelta(hours=1)}
    return admin.Statement.model_validate({**values, **options})


def failure(response, status, code):
    assert (response.status_code, response.json()['code']) == (status, code)


def test_scope_approval_activation_and_timing(live, team, monkeypatch):
    engine = live[0]
    pid, people, _, _ = team
    monkeypatch.setenv('PMO_POLICY_ENVIRONMENT', TARGET)
    monkeypatch.delenv('PMO_SYNTHETIC_POLICY_SCHEMA', raising=False)
    with engine.begin() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id == UUID(pid))).mappings().one()
    action = 'requirement.exception'
    candidate = draft(p, people, [action], 'DEC-09')
    with engine.begin() as c:
        assert not policy.enabled(c, p, action)
        with pytest.raises(ValueError, match='EXPLICIT_POLICY_AUTHORITY_REQUIRED'):
            admin.register(c, candidate, people['pm'], ORIGINAL)
    grants = fixture_authorities(engine, p, people, 'DEC-09')
    candidate = draft(p, people, [action], 'DEC-09')
    with engine.begin() as c:
        for invalid, error in (
            ({'projectId': uuid4()}, 'INVALID_POLICY_PROJECT_SCOPE'),
            ({'orgId': uuid4()}, 'INVALID_POLICY_PROJECT_SCOPE'),
            ({'environment': 'foreign'}, 'POLICY_ENVIRONMENT_MISMATCH'),
            ({'actions': ['cost.reconciliation']}, 'INVALID_POLICY_ACTION_SCOPE'),
            ({'policyHash': '0' * 64}, 'POLICY_OR_EVIDENCE_HASH_MISMATCH'),
            ({'approverId': people['outsider']}, 'EXPLICIT_POLICY_AUTHORITY_REQUIRED'),
        ):
            with pytest.raises(ValueError, match=error):
                admin.register(c, candidate.model_copy(update=invalid), people['pm'], ORIGINAL)
        with pytest.raises(ValueError, match='POLICY_OR_EVIDENCE_HASH_MISMATCH'):
            admin.register(c, candidate, people['pm'], ORIGINAL + b'changed')
        future = draft(p, people, [action], 'DEC-09', effectiveFrom=datetime.now(UTC) + timedelta(minutes=5))
        future_id = admin.register(c, future, people['pm'], ORIGINAL)
        expired = draft(p, people, [action], 'DEC-09', effectiveUntil=datetime.now(UTC) + timedelta(milliseconds=500))
        expired_id = admin.register(c, expired, people['pm'], ORIGINAL)
        identifier = admin.register(c, candidate, people['pm'], ORIGINAL)
        assert not policy.enabled(c, p, action)
        with pytest.raises(ValueError, match='INDEPENDENT_ACTIVATOR_REQUIRED'):
            admin.change_state(c, p.org_id, identifier, people['sponsor'], 'active', 'fixture approval', ORIGINAL)
        with pytest.raises(ValueError, match='POLICY_OR_EVIDENCE_HASH_MISMATCH'):
            admin.change_state(c, p.org_id, identifier, people['reviewer'], 'active', 'fixture approval', ORIGINAL + b'changed')
        with pytest.raises(ValueError, match='POLICY_OUTSIDE_EFFECTIVE_TERM'):
            admin.change_state(c, p.org_id, future_id, people['reviewer'], 'active', 'fixture approval', ORIGINAL)
        admin.change_state(c, p.org_id, expired_id, people['reviewer'], 'active', 'fixture approval', ORIGINAL)
        c.execute(sa.select(sa.func.pg_sleep(0.51)))
        assert not policy.enabled(c, p, action)
        admin.change_state(c, p.org_id, identifier, people['reviewer'], 'active', 'fixture approval', ORIGINAL)
        assert policy.enabled(c, p, action)
        assert not policy.enabled(c, p, 'requirement.customer-confirm')
        assert not policy.enabled(c, SimpleNamespace(id=uuid4(), org_id=p.org_id), action)
        assert not policy.enabled(c, SimpleNamespace(id=p.id, org_id=uuid4()), action)
        admin.revoke_grant(c, p.org_id, grants['approve'], people['outsider'], 'fixture authority withdrawn')
        assert policy.enabled(c, p, action)  # Historical signature remains valid until policy revocation.
    with engine.begin() as c:
        admin.change_state(c, p.org_id, identifier, people['reviewer'], 'revoked', 'fixture policy withdrawn')
        with pytest.raises(Failure) as exc:
            policy.require(c, p, action, uuid4())
        assert exc.value.code == 'POLICY_PENDING'


def test_formal_policy_decision_stale_replacement_replay_and_audit(live, team, monkeypatch, tmp_path):
    engine, _, _, env = live
    pid, people, users, _ = team
    path, _, _ = begin_execution(live, team)
    monkeypatch.setenv('PMO_POLICY_ENVIRONMENT', TARGET)
    monkeypatch.delenv('PMO_SYNTHETIC_POLICY_SCHEMA', raising=False)
    with engine.begin() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id == UUID(pid))).mappings().one()
    fixture_authorities(engine, p, people, 'DEC-19')
    packet = draft(p, people, ['transition.hold'], 'DEC-19')

    with (
        serve(engine.url, tmp_path, evidence_dir=env['PMO_EVIDENCE_DIR'],
              extra_env={'PMO_SYNTHETIC_POLICY_SCHEMA': '', 'PMO_POLICY_ENVIRONMENT': TARGET}) as restricted,
        Client(base_url=restricted.base_url, trust_env=False, cookies=users['pm'].cookies,
               headers={'X-CSRF-Token': users['pm'].headers['X-CSRF-Token']}) as pm,
        Client(base_url=restricted.base_url, trust_env=False, cookies=users['sponsor'].cookies,
               headers={'X-CSRF-Token': users['sponsor'].headers['X-CSRF-Token']}) as sponsor,
    ):
        stale = data(propose(pm, path, 'on_hold'))
        expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        failure(judgment(sponsor, stale, holdUntil=expiry), 403, 'POLICY_PENDING')
        with engine.begin() as c:
            identifier = admin.register(c, packet, people['pm'], ORIGINAL)
            admin.change_state(c, p.org_id, identifier, people['reviewer'], 'active', 'fixture activation', ORIGINAL)
        failure(judgment(sponsor, stale, holdUntil=expiry), 409, 'TRANSITION_STALE')
        current = data(propose(pm, path, 'on_hold'))
        key = headers(current['revision'])
        written = judgment(sponsor, current, key=key, holdUntil=expiry)
        assert data(written)['status'] == 'applied'
        assert written.json() == judgment(sponsor, current, key=key, holdUntil=expiry).json()
        with engine.begin() as c:
            request_id = written.headers['X-Request-ID']
            log = c.execute(sa.select(sa.literal_column('source_refs')).select_from(db.audit_logs).where(
                db.audit_logs.c.request_id == request_id)).scalar_one()
            assert f'policy:{identifier}' in log
            b = policy.bindings.c
            binding = c.scalar(sa.select(b.basis).where(b.subject_id == UUID(request_id), b.kind == 'receipt'))
            assert binding == [{'id': str(identifier), 'policyHash': packet.policyHash,
                                'version': packet.version, 'decisionCode': 'DEC-19',
                                'evidenceHash': packet.evidenceHash}]
            admin.change_state(c, p.org_id, identifier, people['reviewer'], 'revoked', 'fixture revocation')
        failure(judgment(sponsor, current, key=key, holdUntil=expiry), 403, 'POLICY_PENDING')
        assert stage(pm, path)['stage'] == 'on_hold'
        recorded = data(sponsor.get('approvals', params={'projectId': pid, 'approvalId': current['requestId']}))
        assert recorded['items'][0]['decisionId'] == data(written)['decisionId']


def test_policy_shared_lock_serializes_revocation_and_formal_write(live, team, monkeypatch):
    engine = live[0]
    pid, people, _, _ = team
    action = 'requirement.exception'
    monkeypatch.setenv('PMO_POLICY_ENVIRONMENT', TARGET)
    monkeypatch.delenv('PMO_SYNTHETIC_POLICY_SCHEMA', raising=False)
    with engine.begin() as c:
        p = c.execute(sa.select(db.projects).where(db.projects.c.id == UUID(pid))).mappings().one()
    fixture_authorities(engine, p, people, 'DEC-09')
    with engine.begin() as c:
        identifier = admin.register(c, draft(p, people, [action], 'DEC-09'), people['pm'], ORIGINAL)
        admin.change_state(c, p.org_id, identifier, people['reviewer'], 'active', 'fixture activation', ORIGINAL)
    request = uuid4()
    entering = Event()
    blockers = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        def revoke():
            with engine.begin() as c:
                blockers.append(c.scalar(sa.select(sa.func.pg_backend_pid())))
                entering.set()
                return admin.change_state(c, p.org_id, identifier, people['reviewer'], 'revoked', 'fixture revocation')
        with engine.begin() as c:
            policy.require(c, p, action, request)
            waiting = executor.submit(revoke)
            assert entering.wait(timeout=5)
            writer = c.scalar(sa.select(sa.func.pg_backend_pid()))
            deadline = monotonic() + 5
            while writer not in c.scalar(sa.select(sa.func.pg_blocking_pids(blockers[0]))):
                assert monotonic() < deadline
                sleep(0.02)
            assert not waiting.done()
        assert waiting.result(timeout=5)
    with engine.begin() as c:
        assert policy.receipt_refs(c, request) == [f'policy:{identifier}']
        with pytest.raises(Failure) as exc:
            policy.require(c, p, action, uuid4())
        assert exc.value.code == 'POLICY_PENDING'
