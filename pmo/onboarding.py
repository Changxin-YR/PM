from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import Field

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .appointments import (
    MemberRole,
    Role,
    authority,
    authority_scope,
    can_manage_members,
)
from .contracts import Result, Strict
from .identity import Failure, visible
from .materials import finish, replay
from .pagination import pg_offset
from .projects import fingerprint, meta, run

router = APIRouter(prefix='/api/v1', tags=['Onboarding'])
PLACE_PROJECTS = 'human_onboarding_projects'
PLACE_PEOPLE = 'human_onboarding_people'
REVOKE = 'human_member_assignment_revoke'
HUMAN = {'x-actorTypes': ['human']}

revocations = sa.table('project_member_revocations',
    *(sa.column(name, sa.Uuid) for name in ('id', 'member_id', 'revoked_by')),
    sa.column('revoked_at'), sa.column('reason'), sa.column('authority_ref'))


class NominationProject(Strict):
    projectId: UUID
    code: str
    name: str
    revision: int
    stage: str
    roles: list[Role | MemberRole]


class Nominee(Strict):
    personId: UUID
    name: str


class Pagination(Strict):
    total: int
    page: int
    pageSize: int


class NominationProjects(Pagination):
    items: list[NominationProject]


class Nominees(Pagination):
    items: list[Nominee]


class Revoke(Strict):
    personId: UUID
    role: MemberRole
    reason: str = Field(min_length=4, max_length=500)


class Revocation(Strict):
    revocationId: UUID
    personId: UUID
    role: MemberRole
    projectRevision: int
    revokedAt: datetime


def _scope(a):
    p = db.projects
    draft = sa.and_(p.c.stage == 'drafting', p.c.initiator_id == a.person)
    return draft, sa.and_(visible(a), can_manage_members(a))


@router.get('/onboarding/projects', operation_id=PLACE_PROJECTS, openapi_extra=HUMAN)
def nomination_projects(authorization: Auth, page: int = Query(1, ge=1),
                        pageSize: int = Query(20, ge=1, le=100)) -> Result[NominationProjects]:
    def read(c, a, _):
        p = db.projects
        draft, manage = _scope(a)
        rows = sa.select(p.c.id, p.c.code, p.c.name, p.c.revision, p.c.stage, p.c.pm_id, p.c.sponsor_id,
                         draft.label('own_draft')).where(manage)
        total = c.scalar(sa.select(sa.func.count()).select_from(rows.subquery()))
        items = []
        for row in c.execute(rows.order_by(p.c.created_at.desc(), p.c.id).limit(pageSize).offset(pg_offset(page, pageSize))):
            roles: list[Role | MemberRole] = []
            if row.own_draft and row.pm_id is None:
                roles.append('pm')
            if row.own_draft and row.sponsor_id is None:
                roles.append('sponsor')
            roles.extend(('member', 'reviewer', 'client_representative'))
            items.append(NominationProject(projectId=row.id, code=row.code, name=row.name,
                                           revision=row.revision, stage=row.stage, roles=roles))
        return Result(data=NominationProjects(items=items, total=total, page=page, pageSize=pageSize),
                      meta=meta(PLACE_PROJECTS, [f'project:{item.projectId}@{item.revision}' for item in items]))
    return run(authorization, PLACE_PROJECTS, read)


@router.get('/onboarding/people', operation_id=PLACE_PEOPLE, openapi_extra=HUMAN)
def nomination_people(authorization: Auth, keyword: str = Query('', max_length=120),
                      page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=50)) -> Result[Nominees]:
    term = keyword.strip()
    def read(c, a, _):
        items: list[Nominee] = []
        total = 0
        if len(term) >= 2:
            _, manage = _scope(a)
            if not c.scalar(sa.select(db.projects.c.id).where(manage).limit(1)):
                raise Failure(403, 'FORBIDDEN')
            p, u = db.persons, db.user_accounts
            matches = sa.or_(p.c.name.icontains(term, autoescape=True), sa.cast(p.c.id, sa.String) == term.lower())
            rows = sa.select(p.c.id, p.c.name).join(u, p.c.user_id == u.c.id).where(
                p.c.org_id == a.org, p.c.is_active, u.c.is_active, matches)
            total = c.scalar(sa.select(sa.func.count()).select_from(rows.subquery()))
            items = [Nominee(personId=row.id, name=row.name)
                     for row in c.execute(rows.order_by(p.c.name, p.c.id).limit(pageSize).offset(pg_offset(page, pageSize)))]
        return Result(data=Nominees(items=items, total=total, page=page, pageSize=pageSize), meta=meta(PLACE_PEOPLE, []))
    return run(authorization, PLACE_PEOPLE, read)


@router.post('/projects/{projectId}/members/revoke', operation_id=REVOKE, openapi_extra=HUMAN)
def revoke_member(projectId: UUID, command: Revoke, authorization: Auth, key: Key, expected: Revision) -> Result[Revocation]:
    revision = int(expected)
    reason = command.reason.strip()
    signature = fingerprint([str(projectId), str(command.personId), command.role, reason, revision])

    def write(c, a, rid):
        p, m = db.projects, db.project_members
        project = c.execute(sa.select(p).where(p.c.id == projectId, visible(a)).with_for_update(of=p)).mappings().one_or_none()
        if project is None:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        grant_ref = c.scalar(sa.select(authority.c.authority_ref).where(*authority_scope(a, command.role)).with_for_update())
        if not grant_ref or command.personId == a.person:
            raise Failure(403, 'FORBIDDEN', project_id=projectId)
        cached = replay(c, a, REVOKE, key, signature, Result[Revocation])
        if cached:
            return cached
        if not can_revoke_in_stage(project.stage):
            raise Failure(409, 'MEMBERSHIP_STAGE_LOCKED', project_id=projectId)
        if revision != project.revision:
            raise Failure(409, 'VERSION_CONFLICT', project_id=projectId)
        if len(reason) < 4:
            raise Failure(422, 'VALIDATION_ERROR', project_id=projectId)
        member = c.scalar(sa.select(m.c.id).where(m.c.project_id == projectId, m.c.person_id == command.personId,
            m.c.role == command.role, m.c.assignment_status == 'accepted', m.c.accepted_at.is_not(None),
            m.c.revoked_at.is_(None)).with_for_update(of=m))
        if not member:
            raise Failure(404, 'RESOURCE_NOT_FOUND', project_id=projectId)
        when = datetime.now(UTC)
        c.execute(m.update().where(m.c.id == member).values(assignment_status='revoked', revoked_at=when, updated_at=when))
        revoked = c.scalar(revocations.insert().values(member_id=member, revoked_by=a.person, revoked_at=when,
                            reason=reason, authority_ref=grant_ref).returning(revocations.c.id))
        c.execute(p.update().where(p.c.id == projectId).values(revision=revision+1, updated_at=when))
        data = Revocation(revocationId=revoked, personId=command.personId, role=command.role,
                          projectRevision=revision+1, revokedAt=when)
        info = meta(REVOKE, [f'project:{projectId}@{revision+1}', f'member:{member}', f'member_revocation:{revoked}'])
        return finish(c, a, REVOKE, key, rid, projectId, signature, Result[Revocation](data=data, meta=info))
    return run(authorization, REVOKE, write)


def can_revoke_in_stage(stage: str) -> bool:
    return stage not in ('closed', 'retrospective', 'terminated', 'archived', 'rejected')
