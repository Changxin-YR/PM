"""Register verified policy approvals from a protected database-owner terminal."""

import argparse
import json
import os
import sys
from datetime import datetime
from getpass import getpass
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import AwareDatetime, Field, ValidationError

from . import policy
from . import schema_v1 as db
from .audit_store import audit
from .contracts import RULE_VERSION, Strict
from .sessions import hasher

MAX_ORIGINAL_BYTES = 25 * 1024 * 1024


class Statement(Strict):
    orgId: UUID
    projectId: UUID | None = None
    environment: str = Field(min_length=2, max_length=64)
    decisionCode: str
    version: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')
    actions: list[str]
    policyHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidenceRef: str
    evidenceHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    approverId: UUID
    approvedAt: AwareDatetime
    effectiveFrom: AwareDatetime
    effectiveUntil: AwareDatetime | None


def now(c):
    return c.scalar(sa.select(sa.func.clock_timestamp()))


def note(value):
    value = value.strip()
    if not 4 <= len(value) <= 240 or any(ord(char) < 32 for char in value):
        raise ValueError('INVALID_PROTECTED_REFERENCE')
    return value


def account(c, org, person):
    p, u, o = db.persons, db.user_accounts, db.organizations
    return c.scalar(sa.select(u.c.id).select_from(
        p.join(u, u.c.id == p.c.user_id).join(o, o.c.id == p.c.org_id)).where(
        p.c.id == person, p.c.is_active, p.c.org_id == org, u.c.is_active, o.c.status == 'active'
    ).with_for_update(read=True, of=(u, p)))


def lock_accounts(c, org, *people) -> None:
    # Domain transactions authenticate before taking the tenant policy lock.
    # Take the same order during approval and activation.
    for person in sorted(set(people)):
        account(c, org, person)


def log(c, action, org, project, actor, request, reference, digest=None):
    c.execute(audit.insert().values(action=f'policy.{action}', org_id=org, project_id=project, actor_type='human',
        actor_id=actor, request_id=str(request), result='success', rule_version=RULE_VERSION,
        source_refs=[reference], after_hash=digest))


def grant(c, org, registrar, person, capability, scope, reference, original, expires, valid_from=None):
    lock_accounts(c, org, registrar, person)
    policy.lock(c, org, exclusive=True)
    registrar_account = account(c, org, registrar)
    roles = db.user_roles.c
    if (registrar == person or registrar_account is None or not account(c, org, person)
            or not c.scalar(sa.select(roles.id).where(roles.user_id == registrar_account,
                                                     roles.role == 'system_admin').with_for_update(read=True))):
        raise ValueError('INDEPENDENT_AUTHORITY_REGISTRAR_REQUIRED')
    expected = policy.ENVIRONMENT.fullmatch(scope) if capability == 'activate' else scope in policy.TERMS
    if capability not in ('approve', 'verify', 'activate') or not expected:
        raise ValueError('INVALID_AUTHORITY_SCOPE')
    checked_at = now(c)
    valid_from = valid_from or checked_at
    if (expires.tzinfo is None or valid_from.tzinfo is None or expires <= checked_at or valid_from >= expires):
        raise ValueError('AUTHORITY_EXPIRY_REQUIRED')
    if not original:
        raise ValueError('AUTHORITY_ORIGINAL_REQUIRED')
    digest = sha256(original).hexdigest()
    identifier = c.scalar(policy.authority.insert().values(org_id=org, person_id=person, capability=capability,
        scope=scope, authority_ref=note(reference), authority_hash=digest, valid_from=valid_from, expires_at=expires)
        .returning(policy.authority.c.id))
    log(c, 'authority-grant', org, None, registrar_account, uuid4(), f'policy-authority:{identifier}', digest)
    return identifier


def revoke_grant(c, org, identifier, registrar, reference):
    lock_accounts(c, org, registrar)
    policy.lock(c, org, exclusive=True)
    registrar_account = account(c, org, registrar)
    roles = db.user_roles.c
    g = policy.authority.c
    person = c.scalar(sa.select(g.person_id).where(g.id == identifier, g.org_id == org))
    if (person is None or registrar_account is None or registrar == person or not c.scalar(
            sa.select(roles.id).where(roles.user_id == registrar_account, roles.role == 'system_admin')
            .with_for_update(read=True))):
        raise ValueError('INDEPENDENT_AUTHORITY_REGISTRAR_REQUIRED')
    previous = policy.authority_revocations.c
    if c.scalar(sa.select(previous.grant_id).where(previous.grant_id == identifier)):
        raise ValueError('AUTHORITY_ALREADY_REVOKED')
    request = uuid4()
    c.execute(policy.authority_revocations.insert().values(grant_id=identifier, reference=note(reference),
                                                           request_id=request))
    log(c, 'authority-revoke', org, None, registrar_account, request, f'policy-authority:{identifier}')
    return request


def valid_grant(c, org, person, capability, scope, at, *, identifier=None, approved_at=None):
    g, r = policy.authority.c, policy.authority_revocations.c
    if not account(c, org, person):
        raise ValueError('POLICY_ACTOR_INACTIVE')
    criteria = [g.org_id == org, g.person_id == person, g.capability == capability, g.scope == scope,
                g.valid_from <= (approved_at or at), g.expires_at > at,
                ~sa.exists(sa.select(r.grant_id).where(r.grant_id == g.id))]
    if identifier is not None:
        criteria.append(g.id == identifier)
    result = c.scalar(sa.select(g.id).where(*criteria).order_by(g.recorded_at.desc(), g.id).limit(1))
    if result is None:
        raise ValueError('EXPLICIT_POLICY_AUTHORITY_REQUIRED')
    return result


def register(c, statement: Statement, verifier, original):
    org, project = statement.orgId, statement.projectId
    lock_accounts(c, org, statement.approverId, verifier)
    policy.lock(c, org, exclusive=True)
    checked_at = now(c)
    if statement.environment != policy.environment():
        raise ValueError('POLICY_ENVIRONMENT_MISMATCH')
    if project is not None and not c.scalar(sa.select(db.projects.c.id).where(
            db.projects.c.id == project, db.projects.c.org_id == org)):
        raise ValueError('INVALID_POLICY_PROJECT_SCOPE')
    if verifier == statement.approverId:
        raise ValueError('INDEPENDENT_EVIDENCE_VERIFIER_REQUIRED')
    if (statement.approvedAt > checked_at or statement.effectiveFrom < statement.approvedAt
            or statement.effectiveUntil is not None and statement.effectiveUntil <= statement.effectiveFrom):
        raise ValueError('INVALID_POLICY_TERM')
    contents = policy.payload(statement.decisionCode, statement.actions)
    if policy.canonical_hash(contents) != statement.policyHash or not original or (
            sha256(original).hexdigest() != statement.evidenceHash):
        raise ValueError('POLICY_OR_EVIDENCE_HASH_MISMATCH')
    leadership = valid_grant(c, org, statement.approverId, 'approve', statement.decisionCode,
                             checked_at, approved_at=statement.approvedAt)
    verification = valid_grant(c, org, verifier, 'verify', statement.decisionCode, checked_at)
    request = uuid4()
    identifier = c.scalar(policy.versions.insert().values(org_id=org, project_id=project,
        decision_code=statement.decisionCode, version=statement.version, environment=statement.environment,
        evidence_ref=note(statement.evidenceRef), evidence_hash=statement.evidenceHash, approver_id=statement.approverId,
        approved_at=statement.approvedAt, effective_from=statement.effectiveFrom, effective_until=statement.effectiveUntil,
        approval_grant_id=leadership, verified_by=verifier, verification_grant_id=verification,
        policy=contents, policy_hash=statement.policyHash, synthetic=policy.isolated_schema(c),
        request_id=request).returning(policy.versions.c.id))
    log(c, 'approved', org, project, account(c, org, verifier), request, f'policy:{identifier}', statement.policyHash)
    return identifier


def change_state(c, org, identifier, operator, event, reference, original=None):
    v, e = policy.versions.c, policy.events.c
    row = c.execute(sa.select(policy.versions).where(v.id == identifier, v.org_id == org,
        v.environment == policy.environment(), v.synthetic == policy.isolated_schema(c))).mappings().one_or_none()
    if row is None:
        raise ValueError('POLICY_SCOPE_MISMATCH')
    lock_accounts(c, org, operator, *((row.approver_id, row.verified_by) if event == 'active' else ()))
    policy.lock(c, org, exclusive=True)
    checked_at = now(c)
    if operator == row.approver_id:
        raise ValueError('INDEPENDENT_ACTIVATOR_REQUIRED')
    activation = valid_grant(c, org, operator, 'activate', row.environment, checked_at)
    previous = c.scalar(sa.select(e.event).where(e.policy_id == identifier).order_by(e.sequence.desc()).limit(1))
    if event == 'active':
        if previous is not None:
            raise ValueError('POLICY_ALREADY_ACTIVATED')
        if row.effective_from > checked_at or row.effective_until is not None and row.effective_until <= checked_at:
            raise ValueError('POLICY_OUTSIDE_EFFECTIVE_TERM')
        expected = policy.payload(row.decision_code, row.policy['actions'])
        if row.policy != expected or row.policy_hash != policy.canonical_hash(expected) or not original or (
                sha256(original).hexdigest() != row.evidence_hash):
            raise ValueError('POLICY_OR_EVIDENCE_HASH_MISMATCH')
        valid_grant(c, org, row.approver_id, 'approve', row.decision_code, checked_at, identifier=row.approval_grant_id,
                    approved_at=row.approved_at)
        valid_grant(c, org, row.verified_by, 'verify', row.decision_code, checked_at,
                    identifier=row.verification_grant_id)
    elif event != 'revoked' or previous == 'revoked':
        raise ValueError('POLICY_REVOCATION_UNAVAILABLE')
    request = uuid4()
    c.execute(policy.events.insert().values(policy_id=identifier, event=event, person_id=operator,
        activation_grant_id=activation, reason=note(reference), request_id=request))
    log(c, event, org, row.project_id, account(c, org, operator), request, f'policy:{identifier}', row.policy_hash)
    return request


def private_original(path):
    with path.open('rb') as stream:
        data = stream.read(MAX_ORIGINAL_BYTES + 1)
    if not data or len(data) > MAX_ORIGINAL_BYTES:
        raise ValueError('INVALID_PROTECTED_ORIGINAL_SIZE')
    return data


def protected_owner(c):
    if not c.scalar(sa.text('''SELECT current_user = pg_get_userbyid(datdba)
                               FROM pg_database WHERE datname=current_database()''')):
        raise ValueError('DEDICATED_DATABASE_OWNER_REQUIRED')


def authenticate_operator(c, org, person, password: str):
    p, u = db.persons.c, db.user_accounts.c
    digest = c.scalar(sa.select(u.password_hash).join(db.persons, p.user_id == u.id)
        .where(p.id == person, p.org_id == org, p.is_active, u.is_active,
               sa.or_(u.locked_until.is_(None), u.locked_until <= sa.func.clock_timestamp())))
    try:
        if digest and hasher.verify(digest, password):
            return
    except (InvalidHashError, VerificationError):
        pass
    raise ValueError('POLICY_OPERATOR_CREDENTIAL_INVALID')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    routes = parser.add_subparsers(dest='command', required=True)
    draft = routes.add_parser('draft')
    draft.add_argument('decision', choices=policy.TERMS)
    draft.add_argument('--action', action='append', required=True, choices=policy.ACTIONS)
    routes.add_parser('draft-all')
    authority_grant = routes.add_parser('grant-authority')
    authority_grant.add_argument('--org', required=True, type=UUID)
    authority_grant.add_argument('--operator', required=True, type=UUID)
    authority_grant.add_argument('--person', required=True, type=UUID)
    authority_grant.add_argument('--capability', required=True, choices=('approve', 'verify', 'activate'))
    authority_grant.add_argument('--scope', required=True)
    authority_grant.add_argument('--reference', required=True)
    authority_grant.add_argument('--original', required=True, type=Path)
    authority_grant.add_argument('--expires', required=True, type=datetime.fromisoformat)
    authority_grant.add_argument('--valid-from', required=True, type=datetime.fromisoformat)
    authority_revoke = routes.add_parser('revoke-authority')
    authority_revoke.add_argument('--org', required=True, type=UUID)
    authority_revoke.add_argument('--operator', required=True, type=UUID)
    authority_revoke.add_argument('--grant', required=True, type=UUID)
    authority_revoke.add_argument('--reference', required=True)
    approve = routes.add_parser('register-approval')
    approve.add_argument('--statement', required=True, type=Path)
    approve.add_argument('--verifier', required=True, type=UUID)
    approve.add_argument('--original', required=True, type=Path)
    for command in ('activate', 'revoke'):
        action = routes.add_parser(command)
        action.add_argument('--org', required=True, type=UUID)
        action.add_argument('--policy', required=True, type=UUID)
        action.add_argument('--operator', required=True, type=UUID)
        action.add_argument('--reference', required=True)
        action.add_argument('--original', required=command == 'activate', type=Path)
    args = parser.parse_args()
    if args.command in ('draft', 'draft-all'):
        try:
            selected = [(args.decision, args.action)] if args.command == 'draft' else [
                (code, [action for action, dependencies in policy.ACTIONS.items() if code in dependencies])
                for code in policy.TERMS]
            drafts = []
            for code, actions in selected:
                contents = policy.payload(code, actions)
                drafts.append({'status': 'draft', 'policy': contents, 'policyHash': policy.canonical_hash(contents)})
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps(drafts[0] if args.command == 'draft' else drafts, indent=2, ensure_ascii=False))
        return
    if not (target := os.getenv('PMO_MIGRATION_DATABASE_URL')) or policy.environment() is None:
        parser.error('Set PMO_MIGRATION_DATABASE_URL and the target PMO_POLICY_ENVIRONMENT in the protected terminal')
    if not sys.stdin.isatty():
        parser.error('Policy changes require an interactive protected terminal')
    password = getpass('Current PMO operator password: ')
    engine = sa.create_engine(target, hide_parameters=True)
    try:
        with engine.begin() as c:
            protected_owner(c)
            original_path = getattr(args, 'original', None)
            original = private_original(original_path) if original_path else None
            if args.command == 'register-approval':
                statement = Statement.model_validate_json(private_original(args.statement))
                authenticate_operator(c, statement.orgId, args.verifier, password)
            else:
                authenticate_operator(c, args.org, args.operator, password)
            if args.command == 'grant-authority':
                identifier = grant(c, args.org, args.operator, args.person, args.capability, args.scope,
                                   args.reference, original, args.expires, args.valid_from)
            elif args.command == 'revoke-authority':
                identifier = revoke_grant(c, args.org, args.grant, args.operator, args.reference)
            elif args.command == 'register-approval':
                identifier = register(c, statement, args.verifier, original)
            else:
                identifier = change_state(c, args.org, args.policy, args.operator,
                    'active' if args.command == 'activate' else 'revoked', args.reference, original)
    except (ValidationError, OSError, sa.exc.SQLAlchemyError):
        parser.error('POLICY_RECORD_FAILED: verify the protected inputs, uniqueness and target database')
    except ValueError as error:
        parser.error(str(error))
    finally:
        engine.dispose()
    print(f'Policy {args.command} receipt {identifier}')


if __name__ == '__main__':
    main()
