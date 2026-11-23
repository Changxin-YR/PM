from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from sqlalchemy.engine import Connection

from . import schema_v1 as db
from .access import Auth
from .pagination import pg_offset
from .contracts import Result, Strict
from .identity import Actor, Failure
from .materials import project_access
from .projects import meta, run

router = APIRouter(prefix='/api/v1/projects/{projectId}',tags=['Members'])


class Member(Strict):
    personId: UUID
    name: str
    role: str
    acceptedAt: datetime


class Members(Strict):
    items: list[Member]
    page: int
    pageSize: int
    total: int


@router.get('/members', operation_id='human_members_list')
def listing(projectId: UUID, authorization: Auth, page: int=Query(1,ge=1), pageSize: int=Query(20,ge=1,le=100)) -> Result[Members]:
    op = 'human_members_list'

    def query(c: Connection, a: Actor, _: str) -> Result[Members]:
        if a.kind!='human':
            raise Failure(403,'FORBIDDEN')
        project = project_access(c,a,projectId)
        m,p,u = db.project_members,db.persons,db.user_accounts
        rows = sa.select(m.c.id,m.c.person_id,m.c.role,m.c.accepted_at,p.c.name).select_from(
            m.join(p,p.c.id==m.c.person_id).join(u,u.c.id==p.c.user_id)).where(m.c.project_id==projectId,
            p.c.org_id==a.org,p.c.is_active,u.c.is_active,m.c.assignment_status=='accepted',
            m.c.accepted_at.is_not(None),m.c.revoked_at.is_(None),
            sa.or_(m.c.role!='initiator',sa.and_(m.c.person_id==project.initiator_id,project.stage=='drafting')))
        total = c.execute(sa.select(sa.func.count()).select_from(rows.subquery())).scalar_one()
        selected = c.execute(rows.order_by(m.c.role,p.c.name,m.c.id).offset(pg_offset(page,pageSize)).limit(pageSize)).mappings().all()
        return Result[Members](data=Members(items=[Member(personId=r.person_id,name=r.name,role=r.role,acceptedAt=r.accepted_at) for r in selected],
            page=page,pageSize=pageSize,total=total),meta=meta(op,[f'member:{r.id}' for r in selected]))

    return run(authorization,op,query)
