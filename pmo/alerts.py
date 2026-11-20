from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query

from . import schema_v1 as db
from .access import Auth
from .contracts import Result, Strict
from .identity import visible
from .materials import project_access
from .projects import meta, run

router=APIRouter(prefix='/api/v1',tags=['Alerts'])


class Alert(Strict):
    alertId: UUID
    projectId: UUID
    ruleId: str
    severity: str
    status: str
    asOf: datetime
    sourceRefs: list[str]
    projectCode: str | None=None
    notifiedAt: datetime | None=None
    milestoneName: str | None=None
    plannedDate: str | None=None
    forecastDate: str | None=None
    reason: str | None=None


class Page(Strict):
    items: list[Alert]
    page: int
    pageSize: int
    total: int


def listing(auth,pid,page,size):
    op='human_alerts_overview' if pid is None else 'pmo_alert_list'
    def read(c,a,_):
        if pid is not None: project_access(c,a,pid)
        p,e,m=db.projects,db.rule_events,db.milestones
        episode=sa.table('milestone_delay_episodes',sa.column('event_id',sa.Uuid),sa.column('milestone_id',sa.Uuid),sa.column('opening_revision'))
        f=sa.table('milestone_forecasts',sa.column('milestone_id',sa.Uuid),sa.column('revision'),sa.column('planned_date'),sa.column('new_date'),sa.column('reason'))
        stmt=sa.select(e,p.c.code,m.c.name,f.c.planned_date,f.c.new_date,f.c.reason).select_from(p.join(e,e.c.project_id==p.c.id)
            .outerjoin(episode,episode.c.event_id==e.c.id).outerjoin(m,sa.and_(m.c.id==episode.c.milestone_id,m.c.project_id==p.c.id))
            .outerjoin(f,sa.and_(f.c.milestone_id==m.c.id,f.c.revision==episode.c.opening_revision))).where(visible(a))
        if pid is not None: stmt=stmt.where(p.c.id==pid)
        total=c.scalar(sa.select(sa.func.count()).select_from(stmt.subquery()))
        rows=c.execute(stmt.order_by(e.c.created_at.desc(),e.c.id).offset((page-1)*size).limit(size)).mappings()
        items=[]
        for row in rows:
            detail=dict(projectCode=row.code,notifiedAt=row.notified_at,milestoneName=row.name,reason=row.reason,
                plannedDate=str(row.planned_date) if row.planned_date else None,forecastDate=str(row.new_date) if row.new_date else None) if a.kind=='human' else {}
            items.append(Alert(alertId=row.id,projectId=row.project_id,ruleId=row.rule_id,severity={
                'LR-03':'yellow','LR-04':'red','LR-06':'untrusted','LR-08':'yellow'}.get(row.rule_id,'hard'),
                status=row.status,asOf=row.updated_at,sourceRefs=row.source_refs,**detail))
        return Result[Page](data=Page(items=items,page=page,pageSize=size,total=total),meta=meta(op,[f'rule-event:{i.alertId}' for i in items]))
    return run(auth,op,read)


@router.get('/projects/{projectId}/alerts',operation_id='pmo_alert_list',response_model_exclude_none=True)
def project_alerts(projectId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(100,ge=1,le=100))->Result[Page]:
    return listing(authorization,projectId,page,pageSize)


@router.get('/analytics/health-alerts',operation_id='human_alerts_overview',response_model_exclude_none=True)
def overview(authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100))->Result[Page]:
    return listing(authorization,None,page,pageSize)
