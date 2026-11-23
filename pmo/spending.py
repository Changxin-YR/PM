from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query

from . import schema_v1 as db
from .access import Auth
from .pagination import pg_offset
from .contracts import Result, Strict
from .costs import Kind
from .identity import visible
from .projects import meta, run

router=APIRouter(prefix='/api/v1/analytics',tags=['Analytics'])


class Spending(Strict):
    projectId: UUID
    projectCode: str
    postingId: UUID
    occurredAt: datetime
    amountSigned: str
    type: Kind
    sourceRef: str
    approvedAt: datetime | None


class Page(Strict):
    items: list[Spending]
    page: int
    pageSize: int
    total: int
    asOf: datetime


@router.get('/preapproval-spending',operation_id='pmo_preapproval_spending_list',response_model_exclude_none=True)
def spending(authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100))->Result[Page]:
    op='pmo_preapproval_spending_list'
    def read(c,a,_):
        p,t,r,d=db.projects,db.cost_postings,db.reviews,db.decisions
        at=c.scalar(sa.select(sa.func.clock_timestamp()))
        approval=(sa.select(d.c.id.label('decision'),d.c.decided_at.label('approved')).join(r,r.c.id==d.c.review_id)
            .where(d.c.project_id==p.c.id,r.c.project_id==p.c.id,r.c.type=='charter',r.c.status=='decided',
                r.c.verdict.in_(('pass', 'conditional_pass')),d.c.option==r.c.verdict,
                d.c.decided_at<=at).order_by(d.c.decided_at,d.c.id).limit(1).correlate(p).lateral())
        matching=(sa.select(p.c.id.label('pid'),p.c.code,t.c.id,t.c.occurred_at,t.c.amount_signed,t.c.type,t.c.source_ref,approval.c.approved,approval.c.decision)
            .select_from(p.join(t,t.c.project_id==p.c.id).outerjoin(approval,sa.true())).where(visible(a),t.c.status=='verified',
            t.c.verified_at<=at,t.c.occurred_at<=at,sa.or_(approval.c.approved.is_(None),t.c.occurred_at<approval.c.approved)).cte('matching'))
        total=sa.select(sa.func.count().label('total')).select_from(matching).subquery()
        window=sa.select(matching).order_by(matching.c.occurred_at.desc(),matching.c.id).offset(pg_offset(page,pageSize)).limit(pageSize).subquery()
        rows=c.execute(sa.select(total.c.total,window).select_from(total.outerjoin(window,sa.true())).order_by(window.c.occurred_at.desc(),window.c.id)).mappings().all()
        items=[]; refs=[]
        for row in rows:
            if row.id is None: continue
            items.append(Spending(projectId=row.pid,projectCode=row.code,postingId=row.id,occurredAt=row.occurred_at,
                amountSigned=format(row.amount_signed,'.2f'),type=row.type,sourceRef=row.source_ref,approvedAt=row.approved))
            refs.append(f'cost-posting:{row.id}@2')
            if row.decision: refs.append(f'decision:{row.decision}')
        info=meta(op,list(dict.fromkeys(refs))); info.asOf=at
        return Result[Page](data=Page(items=items,page=page,pageSize=pageSize,total=rows[0].total,asOf=at),meta=info)
    return run(authorization,op,read)
