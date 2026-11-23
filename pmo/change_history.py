from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import ConfigDict, JsonValue, model_serializer

from . import schema_v1 as db
from .access import Auth
from .pagination import pg_offset
from .change_contract import HISTORY, Target, legacy_snapshot
from .contracts import Result, Strict
from .identity import Failure, visible
from .projects import meta, run

router=APIRouter(prefix='/api/v1',tags=['Changes'])
decisions=sa.table('change_decision_records',sa.column('change_id',sa.Uuid),sa.column('approver_id',sa.Uuid),sa.column('rationale'),sa.column('decided_at',sa.DateTime(timezone=True)))


def with_decision(t):
    return sa.select(t,decisions.c.approver_id,decisions.c.rationale,decisions.c.decided_at).select_from(t.outerjoin(decisions,decisions.c.change_id==t.c.id))


class Entry(Strict):
    model_config = ConfigDict(json_schema_extra=HISTORY)
    changeId: UUID
    projectId: UUID
    targetType: Target
    targetId: UUID
    before: JsonValue
    after: JsonValue
    legacySnapshot: Literal[True] | None = None
    status: str
    reason: str
    baseRevision: int
    revision: int
    requestedBy: UUID
    approvedBy: UUID | None = None
    createdAt: datetime
    effectiveAt: datetime | None = None
    decidedBy: UUID | None = None
    decisionReason: str | None = None
    decidedAt: datetime | None = None

    @model_serializer(mode='wrap')
    def include_json_null_snapshots(self, serialize):
        # A JSON null in an old snapshot is data, not an absent DTO field.
        return {**serialize(self), 'before': self.before, 'after': self.after}


class Page(Strict):
    items: list[Entry]
    page: int
    pageSize: int
    total: int
    asOf: datetime


def entry(i):
    return Entry(changeId=i.id, projectId=i.project_id, targetType=i.target_type, targetId=i.target_id,
        before=i.before, after=i.after, legacySnapshot=legacy_snapshot(i.target_type, i.before, i.after),
        status=i.status, reason=i.reason, baseRevision=i.base_revision, revision=i.revision, requestedBy=i.requested_by,
        approvedBy=i.approved_by, createdAt=i.created_at, effectiveAt=i.effective_at, decidedBy=i.get('approver_id'),
        decisionReason=i.get('rationale'), decidedAt=i.get('decided_at'))


@router.get('/requirements/{requirementId}/changes',operation_id='pmo_requirement_change_history',response_model_exclude_none=True)
def history(requirementId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(100,ge=1,le=100))->Result[Page]:
    op='pmo_requirement_change_history'
    def read(c,a,_):
        p,r,t=db.projects,db.requirements,db.change_entries
        target=sa.select(r.c.id,r.c.project_id).join(p,p.c.id==r.c.project_id).where(r.c.id==requirementId,visible(a)).cte('target')
        matched=with_decision(t).join(target,sa.and_(t.c.target_id==target.c.id,t.c.project_id==target.c.project_id)).where(t.c.target_type=='requirement').cte('matched')
        counts=sa.select(sa.func.count().label('total')).select_from(matched).subquery()
        rows=sa.select(matched).order_by(matched.c.created_at.desc(),matched.c.id.desc()).offset(pg_offset(page,pageSize)).limit(pageSize).subquery()
        records=c.execute(sa.select(counts.c.total,rows,target.c.project_id.label('authorized_project_id'),sa.func.statement_timestamp().label('at')).select_from(
            target.join(counts,sa.true()).outerjoin(rows,sa.true())).order_by(rows.c.created_at.desc(),rows.c.id.desc())).mappings().all()
        if not records: raise Failure(404,'RESOURCE_NOT_FOUND')
        items=[entry(i) for i in records if i.id is not None]
        at=records[0].at; info=meta(op,[f'change:{i.changeId}@{i.revision}' for i in items],project_id=records[0].authorized_project_id); info.asOf=at
        return Result[Page](data=Page(items=items,page=page,pageSize=pageSize,total=records[0].total,asOf=at),meta=info)
    return run(authorization,op,read)
