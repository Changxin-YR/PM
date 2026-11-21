"""Browser-only organization directory and account suspension."""
from datetime import datetime
from typing import Annotated, Generic, TypeVar
from uuid import UUID, uuid5

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AfterValidator, Field, StrictBool, StringConstraints

from . import org_events, schema_v1 as db
from .access import Auth, Key, Revision
from .agent_tokens import events as token_events
from .contracts import Result, Strict
from .identity import Failure
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .reports import _plain
from .session_schema import sessions

USERS, MEMBERS, STATUS = 'admin_users_list', 'admin_members_list', 'admin_user_update_status'
ADMIN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True, 'x-requiredRole': 'system_admin'}
router = APIRouter(prefix='/api/v1/admin', tags=['Organization accounts'])
users = sa.table('user_accounts', sa.column('id', sa.Uuid), sa.column('username'), sa.column('is_active', sa.Boolean),
    sa.column('password_hash'), sa.column('status_revision', sa.BigInteger), sa.column('created_at', sa.DateTime(timezone=True)),
    sa.column('updated_at', sa.DateTime(timezone=True)), sa.column('last_login_at', sa.DateTime(timezone=True)),
    sa.column('locked_until', sa.DateTime(timezone=True)))
u, p, ur = users.c, db.persons.c, db.user_roles.c
status_changes = sa.table('account_status_changes', sa.column('account_id', sa.Uuid), sa.column('revision', sa.BigInteger),
    sa.column('changed_by', sa.Uuid), sa.column('request_id', sa.Uuid), sa.column('is_active', sa.Boolean), sa.column('reason'))
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=500), AfterValidator(_plain)]
T = TypeVar('T')


class Directory(Strict, Generic[T]):
    items: list[T] = Field(max_length=100)
    page: int
    pageSize: int
    total: int


class Member(Strict):
    personId: UUID
    name: str
    isActive: bool
    revision: int
    displayRole: str | None = None
    userId: UUID | None = None


class User(Strict):
    userId: UUID
    personId: UUID
    username: str
    name: str
    isActive: bool
    personActive: bool
    isSystemAdmin: bool
    revision: int
    canChangeStatus: bool
    lastLoginAt: datetime | None = None


class Status(Strict):
    isActive: StrictBool
    reason: Reason


def admin(c, a):
    if a.kind != 'human' or not c.scalar(sa.select(ur.id).where(ur.user_id == a.account,
            ur.role == 'system_admin').with_for_update(read=True)):
        raise Failure(403, 'FORBIDDEN')


def user_query():
    authorized = sa.exists(sa.select(ur.id).where(ur.user_id == u.id, ur.role == 'system_admin')).label('administrator')
    return sa.select(u.id, u.username, u.is_active, u.last_login_at, u.status_revision, p.id.label('person_id'),
        p.name, p.is_active.label('person_active'), authorized).select_from(users.join(db.persons, p.user_id == u.id))


def user_view(row, account):
    return User(userId=row.id, personId=row.person_id, username=row.username, name=row.name,
        isActive=row.is_active, personActive=row.person_active, isSystemAdmin=row.administrator,
        revision=row.status_revision, canChangeStatus=row.id != account and not row.administrator, lastLoginAt=row.last_login_at)


def org_receipt(c, a, op, rid, key, sig, entity, item, event, *, before=None, resource='account'):
    result = Result(data=item, meta=meta(op, [f'organization:{a.org}', f'{resource}:{entity}@{item.revision}']))
    body = result.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, entity, None, sig, body, fingerprint(body), before)
    c.execute(db.outbox_events.insert().values(dedupe_key=rid, event_type=event, status='pending',
        payload={'orgId': str(a.org), 'resourceId': str(entity), 'revision': item.revision}))
    return result


@router.get('/users', operation_id=USERS, openapi_extra=ADMIN, response_model_exclude_none=True)
def user_list(authorization: Auth, page: int = Query(1, ge=1, le=10000),
              pageSize: int = Query(20, ge=1, le=100)) -> Result[Directory[User]]:
    def read(c, a, _):
        admin(c, a)
        q = user_query().where(p.org_id == a.org)
        total = c.scalar(sa.select(sa.func.count()).select_from(q.subquery()))
        items = [user_view(row, a.account) for row in c.execute(q.order_by(u.username, u.id)
            .offset((page-1)*pageSize).limit(pageSize)).mappings()]
        return Result[Directory[User]](data=Directory[User](items=items, total=total, page=page, pageSize=pageSize),
            meta=meta(USERS, [f'account:{item.userId}@{item.revision}' for item in items]))
    return run(authorization, USERS, read)


@router.get('/members', operation_id=MEMBERS, openapi_extra=ADMIN, response_model_exclude_none=True)
def member_list(authorization: Auth, page: int = Query(1, ge=1, le=10000),
                pageSize: int = Query(20, ge=1, le=100)) -> Result[Directory[Member]]:
    def read(c, a, _):
        admin(c, a)
        q = sa.select(p.id, p.name, p.role, p.is_active, p.user_id, p.revision).where(p.org_id == a.org)
        total = c.scalar(sa.select(sa.func.count()).select_from(q.subquery()))
        items = [Member(personId=row.id, name=row.name, displayRole=row.role, isActive=row.is_active, userId=row.user_id,
            revision=row.revision) for row in c.execute(q.order_by(p.name, p.id).offset((page-1)*pageSize).limit(pageSize)).mappings()]
        return Result[Directory[Member]](data=Directory[Member](items=items, total=total, page=page, pageSize=pageSize),
            meta=meta(MEMBERS, [f'person:{item.personId}@{item.revision}' for item in items]))
    return run(authorization, MEMBERS, read)


def revoke_identity(c, who, by, rid, reason, at):
    c.execute(sessions.update().where(sessions.c.account_id == who.id, sessions.c.revoked_at.is_(None)).values(revoked_at=at))
    t = db.agent_credentials
    revoked = c.scalars(t.update().where(t.c.delegated_by == who.id, t.c.org_id == by.org, t.c.revoked_at.is_(None),
        t.c.status != 'revoked').values(status='revoked', revoked_at=at).returning(t.c.id)).all()
    if revoked:
        c.execute(token_events.insert(), [{'token_id': token, 'actor_id': by.account, 'action': 'revoke',
            'request_id': uuid5(UUID(rid), str(token)), 'reason': reason} for token in revoked])


@router.patch('/users/{userId}/status', operation_id=STATUS, openapi_extra=ADMIN, response_model_exclude_none=True)
def update_status(userId: UUID, command: Status, authorization: Auth, key: Key, expected: Revision) -> Result[User]:
    rev = int(expected)
    sig = fingerprint([str(userId), rev, command.model_dump(mode='json')])

    def write(c, a, rid):
        admin(c, a)
        query = user_query().where(p.org_id == a.org, u.id == userId)
        current = c.execute(query).mappings().one_or_none()
        if current is None:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        # No admin mutations here, including self. Avoid taking another admin's account lock.
        if userId == a.account or current.administrator:
            raise Failure(403, 'ADMIN_ACCOUNT_PROTECTED')
        current = c.execute(query.with_for_update(of=users, key_share=True)).mappings().one()
        if old := replay(c, a, STATUS, key, sig, Result[User]):
            return old
        if current.status_revision != rev:
            raise Failure(409, 'VERSION_CONFLICT')
        if current.is_active == command.isActive:
            raise Failure(409, 'ACCOUNT_STATUS_UNCHANGED')
        if command.isActive and not current.person_active:
            raise Failure(422, 'PERSON_INACTIVE')
        at = c.scalar(sa.select(sa.func.clock_timestamp()))
        c.execute(users.update().where(u.id == userId, u.status_revision == rev)
            .values(is_active=command.isActive, status_revision=rev+1, updated_at=at))
        if not command.isActive:
            revoke_identity(c, current, a, rid, command.reason, at)
        c.execute(status_changes.insert().values(account_id=userId, revision=rev+1, changed_by=a.account, request_id=UUID(rid),
            is_active=command.isActive, reason=command.reason))
        item = user_view(c.execute(query).mappings().one(), a.account)
        before = fingerprint([str(userId), current.status_revision, current.is_active])
        return org_receipt(c, a, STATUS, rid, key, sig, userId, item, org_events.STATUS_CHANGED, before=before)

    return run(authorization, STATUS, write)
