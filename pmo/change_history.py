from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query

from . import schema_v1 as db
from .access import Auth
from .contracts import Result, Strict
from .identity import Failure, visible
from .projects import meta, run

router=APIRouter(prefix='/api/v1',tags=['Changes'])


class Entry(Strict):
    changeId: UUID
    projectId: UUID
    targetType: str
    targetId: UUID
    before: dict
    after: dict
    status: str
    reason: str
    baseRevision: int
    revision: int
    requestedBy: UUID
    approvedBy: UUID | None = None
    createdAt: datetime
    effectiveAt: datetime | None = None


class Page(Strict):
    items: list[Entry]
    page: int
    pageSize: int
    total: int
    asOf: datetime


@router.get('/requirements/{requirementId}/changes',operation_id='pmo_requirement_change_history',response_model_exclude_none=True)
def history(requirementId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(100,ge=1,le=100))->Result[Page]:
    op='pmo_requirement_change_history'
    def read(c,a,_):
        p,r,t=db.projects,db.requirements,db.change_entries
        target=sa.select(r.c.id,r.c.project_id).join(p,p.c.id==r.c.project_id).where(r.c.id==requirementId,visible(a)).cte('target')
        matched=sa.select(t).join(target,sa.and_(t.c.target_id==target.c.id,t.c.project_id==target.c.project_id)).where(t.c.target_type=='requirement').cte('matched')
        counts=sa.select(sa.func.count().label('total')).select_from(matched).subquery()
        rows=sa.select(matched).order_by(matched.c.created_at.desc(),matched.c.id.desc()).offset((page-1)*pageSize).limit(pageSize).subquery()
        records=c.execute(sa.select(counts.c.total,rows,sa.func.statement_timestamp().label('at')).select_from(
            target.join(counts,sa.true()).outerjoin(rows,sa.true())).order_by(rows.c.created_at.desc(),rows.c.id.desc())).mappings().all()
        if not records: raise Failure(404,'RESOURCE_NOT_FOUND')
        items=[Entry(changeId=i.id,projectId=i.project_id,targetType=i.target_type,targetId=i.target_id,before=i.before,after=i.after,
            status=i.status,reason=i.reason,baseRevision=i.base_revision,revision=i.revision,requestedBy=i.requested_by,approvedBy=i.approved_by,
            createdAt=i.created_at,effectiveAt=i.effective_at) for i in records if i.id is not None]
        at=records[0].at; info=meta(op,[f'change:{i.changeId}@{i.revision}' for i in items]); info.asOf=at
        return Result[Page](data=Page(items=items,page=page,pageSize=pageSize,total=records[0].total,asOf=at),meta=info)
    return run(authorization,op,read)
