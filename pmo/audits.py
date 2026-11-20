from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from sqlalchemy.engine import Connection

from . import schema_v1 as db
from .access import Auth
from .contracts import Result, Strict
from .identity import Actor, Failure
from .projects import meta, run

router = APIRouter(prefix='/api/v1/admin',tags=['Administration'])
Outcome = Literal['success','denied','failed']


def is_admin(c: Connection, a: Actor) -> bool:
    r = db.user_roles
    return a.kind=='human' and bool(c.scalar(sa.select(r.c.id).where(r.c.user_id==a.account,r.c.role=='system_admin')))


class AuditEntry(Strict):
    auditId: UUID
    createdAt: datetime
    requestId: str
    actorType: Literal['human','agent']
    actorId: UUID
    delegatedBy: UUID | None
    projectId: UUID | None
    action: str
    result: Outcome
    errorCode: str | None


class AuditPage(Strict):
    items: list[AuditEntry]
    total: int
    page: int
    pageSize: int


@router.get('/audits',operation_id='admin_audits_list',response_model_exclude_none=True)
def listing(authorization: Auth, page: int=Query(1,ge=1), pageSize: int=Query(20,ge=1,le=100),
            requestId: UUID | None=None, result: Outcome | None=None) -> Result[AuditPage]:
    op='admin_audits_list'
    def query(c: Connection,a: Actor,_: str) -> Result[AuditPage]:
        if not is_admin(c,a):
            raise Failure(403,'FORBIDDEN')
        log=db.audit_logs.c
        conditions=[log.org_id==a.org]
        if requestId is not None:
            conditions.append(log.request_id==str(requestId))
        if result is not None:
            conditions.append(log.result==result)
        n=c.execute(sa.select(sa.func.count()).select_from(db.audit_logs).where(*conditions)).scalar_one()
        stmt=sa.select(log.id,log.created_at,log.request_id,log.actor_type,log.actor_id,log.delegated_by,
            log.project_id,log.action,log.result,log.error_code).where(*conditions).order_by(log.created_at.desc(),log.id)
        items=[AuditEntry(auditId=r.id,createdAt=r.created_at,requestId=r.request_id,actorType=r.actor_type,
               actorId=r.actor_id,delegatedBy=r.delegated_by,projectId=r.project_id,action=r.action,
               result=r.result,errorCode=r.error_code) for r in c.execute(stmt.limit(pageSize).offset((page-1)*pageSize)).mappings()]
        return Result[AuditPage](data=AuditPage(items=items,page=page,pageSize=pageSize,total=n),meta=meta(op,[f'audit:{i.auditId}' for i in items]))
    return run(authorization,op,query)
