"""One-time membership invitations; redeeming one never grants a business role."""
from datetime import datetime, timedelta
from hashlib import sha256
import os
import re
from secrets import token_urlsafe
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request, Response
from pydantic import AfterValidator, Field, SecretStr, StringConstraints, field_validator
from sqlalchemy.dialects.postgresql import insert

from . import org_events, schema_v1 as db, sessions
from .access import Auth, Key, Revision
from .pagination import pg_offset
from .contracts import RULE_VERSION, Result, Strict
from .db import engine
from .identity import Actor, Failure
from .materials import replay
from .org_accounts import ADMIN, Directory, Reason, User, admin, org_receipt, u, ur, user_query, user_view, users
from .projects import fingerprint, meta, run
from .reports import _plain
from .request_context import correlation_id

INVITE = 'admin_user_invite'
LIST = 'admin_user_invitation_list'
REVOKE = 'admin_user_invitation_revoke'
ACCEPT = 'auth_accept_user_invitation'
router = APIRouter(prefix='/api/v1', tags=['Organization invitations'])
invites = sa.table('organization_user_invitations', sa.column('id', sa.Uuid), sa.column('org_id', sa.Uuid),
    sa.column('email'), sa.column('role_hint'), sa.column('issuer_id', sa.Uuid), sa.column('code_hash'),
    sa.column('revision', sa.BigInteger), sa.column('issued_at', sa.DateTime(timezone=True)),
    sa.column('expires_at', sa.DateTime(timezone=True)), sa.column('account_id', sa.Uuid),
    sa.column('accepted_at', sa.DateTime(timezone=True)), sa.column('revoked_at', sa.DateTime(timezone=True)),
    sa.column('revoked_by', sa.Uuid), sa.column('revocation_reason'))
i = invites.c
RoleHint = Literal['member', 'pm', 'reviewer', 'sponsor', 'client_representative']
Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=254),
                  Field(json_schema_extra={'format': 'email'})]
EMAIL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
                   r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?')


class Invite(Strict):
    email: Email
    role: RoleHint = 'member'

    @field_validator('email')
    @classmethod
    def email_address(cls, value: str) -> str:
        if not EMAIL.fullmatch(value) or len(value.split('@', 1)[0]) > 64:
            raise ValueError('Expected a deliverable address format')
        return value.lower()


class Invitation(Strict):
    invitationId: UUID
    email: str
    roleHint: RoleHint
    revision: int
    issuedAt: datetime
    expiresAt: datetime
    state: Literal['pending', 'expired', 'accepted', 'revoked']


class Revoke(Strict):
    reason: Reason


class Accept(Strict):
    invitationCode: SecretStr = Field(min_length=43, max_length=43)
    username: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True)] = Field(
        min_length=3, max_length=120, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    displayName: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120), AfterValidator(_plain)]
    password: SecretStr = Field(min_length=14, max_length=256)

    @field_validator('invitationCode', 'password')
    @classmethod
    def credential_encoding(cls, value: SecretStr) -> SecretStr:
        _plain(value.get_secret_value())
        return value


def view(row, now):
    state = ('accepted' if row.accepted_at else 'revoked' if row.revoked_at
             else 'expired' if row.expires_at <= now else 'pending')
    return Invitation(invitationId=row.id, email=row.email, roleHint=row.role_hint, revision=row.revision,
        issuedAt=row.issued_at, expiresAt=row.expires_at, state=state)


@router.get('/admin/users/invitations', operation_id=LIST, openapi_extra=ADMIN)
def listing(authorization: Auth, page: int = Query(1, ge=1, le=10000),
            pageSize: int = Query(20, ge=1, le=100)) -> Result[Directory[Invitation]]:
    def read(c, a, _):
        admin(c, a)
        scope = i.org_id == a.org
        count = c.scalar(sa.select(sa.func.count()).select_from(invites).where(scope))
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        rows = c.execute(sa.select(invites).where(scope).order_by(i.issued_at.desc(), i.id.desc())
            .offset(pg_offset(page,pageSize)).limit(pageSize)).mappings()
        items = [view(row, now) for row in rows]
        return Result[Directory[Invitation]](data=Directory[Invitation](items=items, page=page, pageSize=pageSize, total=count),
            meta=meta(LIST, [f'invitation:{item.invitationId}@{item.revision}' for item in items]))
    return run(authorization, LIST, read)


@router.post('/admin/users/invitations', operation_id=INVITE, openapi_extra=ADMIN)
def issue(command: Invite, response: Response, authorization: Auth, key: Key) -> Result[Invitation]:
    sig = fingerprint(command.model_dump(mode='json'))

    def write(c, a, rid):
        admin(c, a)
        if saved := replay(c, a, INVITE, key, sig, Result[Invitation]):
            return saved, None
        c.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(f'org-invite:{a.org}:{command.email}', 0))))
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        if c.scalar(sa.select(i.id).where(i.org_id == a.org, i.email == command.email,
                sa.or_(i.account_id.is_not(None), sa.and_(i.revoked_at.is_(None), i.expires_at > now))).limit(1)):
            raise Failure(409, 'INVITATION_ALREADY_EXISTS')
        code = token_urlsafe(32)
        row = c.execute(invites.insert().values(org_id=a.org, email=command.email, role_hint=command.role, issuer_id=a.account,
            code_hash=sha256(code.encode()).hexdigest(), issued_at=now, expires_at=now+timedelta(hours=24))
            .returning(invites)).mappings().one()
        return org_receipt(c, a, INVITE, rid, key, sig, row.id, view(row, now), org_events.INVITED, resource='invitation'), code

    result, code = run(authorization, INVITE, write)
    if code:
        response.headers['X-PMO-Org-Invitation'] = code
    return result


@router.post('/admin/users/invitations/{invitationId}/revoke', operation_id=REVOKE, openapi_extra=ADMIN)
def revoke(invitationId: UUID, command: Revoke, authorization: Auth, key: Key, expected: Revision) -> Result[Invitation]:
    rev = int(expected)
    sig = fingerprint([str(invitationId), rev, command.reason])

    def write(c, a, rid):
        admin(c, a)
        row = c.execute(sa.select(invites).where(i.org_id == a.org, i.id == invitationId).with_for_update()).mappings().one_or_none()
        if row is None:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        if saved := replay(c, a, REVOKE, key, sig, Result[Invitation]):
            return saved
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        if row.revision != rev:
            raise Failure(409, 'VERSION_CONFLICT')
        if view(row, now).state != 'pending':
            raise Failure(409, 'INVITATION_NOT_PENDING')
        saved = c.execute(invites.update().where(i.id == row.id, i.org_id == a.org, i.revision == rev)
            .values(revision=2, revoked_at=now, revoked_by=a.account, revocation_reason=command.reason)
            .returning(invites)).mappings().one()
        return org_receipt(c, a, REVOKE, rid, key, sig, row.id, view(saved, now), org_events.INVITE_REVOKED, resource='invitation')
    return run(authorization, REVOKE, write)


def _issuer(c, row):
    who = db.persons.c
    o = db.organizations.c
    account = c.execute(sa.select(u.id, who.id.label('person_id')).select_from(users.join(db.persons, who.user_id==u.id)
        .join(db.organizations, o.id==who.org_id)).where(u.id==row.issuer_id, who.org_id==row.org_id,
            u.is_active, who.is_active, o.status=='active', sa.or_(u.locked_until.is_(None), u.locked_until<=sa.func.now()))
        .with_for_update(of=users, key_share=True)).mappings().one_or_none()
    if account and c.scalar(sa.select(ur.id).where(ur.user_id==account.id, ur.role=='system_admin').with_for_update(read=True)):
        return account.person_id
    raise Failure(403, 'INVITATION_INVALID')


@router.post('/auth/invitations/accept', operation_id=ACCEPT, response_model_exclude_none=True,
             openapi_extra={'x-actorTypes':['human'], 'x-forbidAgentEvenViaHttp':True})
def accept(command: Accept, request: Request, key: Key, expected: Revision) -> Result[User]:
    if request.headers.get('Authorization'):
        raise Failure(403, 'FORBIDDEN')
    local = request.url.hostname in ('127.0.0.1', 'localhost', '::1') and os.getenv('PMO_LOCAL_HTTP')=='1'
    if not local and request.url.scheme != 'https':
        raise Failure(403, 'HTTPS_REQUIRED')
    secret = command.invitationCode.get_secret_value()
    digest = sha256(secret.encode()).hexdigest()
    rev, rid = int(expected), correlation_id()
    # Including the secret in the signature prevents an offline password test from the receipt alone.
    sig = fingerprint([secret, rev, command.username, command.displayName, command.password.get_secret_value()])
    with engine().begin() as c:
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        ip = request.client.host if request.client else 'unknown'
        allowed = sessions.throttle(c, 'org-invitation-ip:'+ip, 30, now) and sessions.throttle(c, 'org-invitation:'+digest, 10, now)
    if not allowed:
        raise Failure(429, 'RATE_LIMITED')

    row = None
    try:
        with engine().begin() as c:
            if not re.fullmatch(r'[a-zA-Z0-9_-]{43}', secret):
                raise Failure(403, 'INVITATION_INVALID')
            row = c.execute(sa.select(invites).where(i.code_hash == digest)).mappings().one_or_none()
            if row is None:
                raise Failure(403, 'INVITATION_INVALID')
            creator = _issuer(c, row)
            row = c.execute(sa.select(invites).where(i.id == row.id).with_for_update()).mappings().one()
            at = c.scalar(sa.select(sa.func.clock_timestamp()))
            if row.expires_at <= at or row.revoked_at:
                raise Failure(403, 'INVITATION_INVALID')
            if row.account_id:
                prior_actor = Actor(row.account_id, creator, row.account_id, row.org_id, frozenset(), (), False, 'human')
                if saved := replay(c, prior_actor, ACCEPT, key, sig, Result[User]):
                    return saved
                raise Failure(403, 'INVITATION_INVALID')
            if row.revision != rev:
                raise Failure(409, 'VERSION_CONFLICT')
            name = command.username.lower()
            if c.scalar(sa.select(u.id).where(sa.func.lower(u.username) == name).limit(1)):
                raise Failure(409, 'USERNAME_UNAVAILABLE')
            account = c.scalar(insert(users).values(username=name, is_active=True,
                password_hash=sessions.hasher.hash(command.password.get_secret_value()))
                .on_conflict_do_nothing(index_elements=[u.username]).returning(u.id))
            if account is None:
                raise Failure(409, 'USERNAME_UNAVAILABLE')
            person = c.scalar(db.persons.insert().values(user_id=account, org_id=row.org_id, name=command.displayName,
                is_active=True, created_by=creator, role=row.role_hint).returning(db.persons.c.id))
            c.execute(invites.update().where(i.id == row.id, i.revision == rev).values(revision=2, accepted_at=at, account_id=account))
            actor = Actor(account, person, account, row.org_id, frozenset(), (), False, 'human')
            result = user_view(c.execute(user_query().where(u.id == account)).mappings().one(), account)
            return org_receipt(c, actor, ACCEPT, rid, key, sig, account, result, org_events.ENROLLED)
    except Failure as exc:
        if row is not None:
            with engine().begin() as c:
                c.execute(db.audit_logs.insert().values(action=ACCEPT, actor_type='human', actor_id=row.id,
                    org_id=row.org_id, request_id=rid, result='denied', error_code=exc.code, rule_version=RULE_VERSION))
        raise
