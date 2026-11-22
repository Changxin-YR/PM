from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query

from . import schema_v1 as db
from .access import Auth
from .contracts import Result, Strict
from .health_schema import FEASIBILITY_OVERDUE, events
from .identity import Failure, visible
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
    businessPeriod: str | None=None
    dueAt: datetime | None=None
    taskTitle: str | None=None


class Page(Strict):
    items: list[Alert]
    page: int
    pageSize: int
    total: int
    asOf: datetime


def _page_rows(c, a, pid, page, size, rule, month):
    p, e, m = db.projects, events, db.milestones
    authorized = sa.select(p.c.id, p.c.code).where(visible(a))
    if pid is not None:
        authorized = authorized.where(p.c.id == pid)
    scope = authorized.cte('alert_project_scope')
    candidates = sa.select(e.c.id, e.c.created_at, scope.c.code).select_from(scope.join(e, e.c.project_id == scope.c.id))
    if rule:
        candidates = candidates.where(e.c.rule_id == rule)
    if month:
        # LR-08 belongs to the deadline month, even when a later scan catches it up.
        created_month = sa.func.to_char(sa.func.timezone('Asia/Shanghai', e.c.created_at), 'YYYY-MM')
        candidates = candidates.where(sa.or_(sa.and_(e.c.rule_id == 'LR-08', e.c.business_period.like(f'{month}-%')),
                                             sa.and_(e.c.rule_id != 'LR-08', created_month == month)))
    matched = candidates.cte('scoped_alerts')
    counts = sa.select(sa.func.count().label('total')).select_from(matched).subquery()
    keys = sa.select(matched).order_by(matched.c.created_at.desc(), matched.c.id).limit(size).offset(
        min((page - 1) * size, 2**63 - 1)).subquery('alert_page_keys')
    # Retain the scoped count and statement time even when the page has no rows.
    rows_from = counts.outerjoin(keys, sa.true()).outerjoin(e, e.c.id == keys.c.id)
    details = []
    if a.kind == 'human':
        episode = sa.table('milestone_delay_episodes', sa.column('event_id', sa.Uuid), sa.column('milestone_id', sa.Uuid),
                           sa.column('opening_revision'))
        f = sa.table('milestone_forecasts', sa.column('milestone_id', sa.Uuid), sa.column('revision'),
                     sa.column('planned_date'), sa.column('new_date'), sa.column('reason'))
        rows_from = rows_from.outerjoin(episode, episode.c.event_id == e.c.id).outerjoin(
            m, sa.and_(m.c.id == episode.c.milestone_id, m.c.project_id == e.c.project_id)).outerjoin(
            f, sa.and_(f.c.milestone_id == m.c.id, f.c.revision == episode.c.opening_revision))
        details = [keys.c.code, m.c.name, f.c.planned_date, f.c.new_date, f.c.reason]
    can_read = sa.exists(sa.select(scope.c.id)) if pid is not None else sa.true()
    rows = c.execute(sa.select(e, *details, counts.c.total, can_read.label('authorized'),
        sa.func.statement_timestamp().label('read_at')).select_from(rows_from).order_by(
            keys.c.created_at.desc(), keys.c.id)).mappings().all()
    if not rows[0].authorized:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return rows


def listing(auth,pid,page,size,rule=None,month=None):
    op='human_alerts_overview' if pid is None else 'pmo_alert_list'
    def read(c,a,_):
        rows = _page_rows(c, a, pid, page, size, rule, month)
        items=[]
        for row in rows:
            if row.id is None:
                continue
            info=row.input_snapshot or {}
            detail=dict(projectCode=row.code,notifiedAt=row.notified_at,milestoneName=info.get('milestoneName',row.name),
                reason=info.get('reason') or row.reason,businessPeriod=row.business_period,
                plannedDate=str(row.planned_date) if row.planned_date else info.get('plannedDate'),
                forecastDate=str(row.new_date) if row.new_date else info.get('forecastDate'),
                dueAt=info.get('dueAt'),taskTitle=info.get('taskTitle')) if a.kind=='human' else {}
            items.append(Alert(alertId=row.id,projectId=row.project_id,ruleId=row.rule_id,severity={
                'LR-03':'yellow','LR-04':'red','LR-06':'untrusted','LR-08':'yellow'}.get(row.rule_id,
                    'yellow' if row.rule_id=='LR-02' and info.get('kind')==FEASIBILITY_OVERDUE else 'hard'),
                status=row.status,asOf=row.updated_at,sourceRefs=row.source_refs if a.kind=='human' else row.source_refs[:100],**detail))
        info = meta(op, [f'rule-event:{i.alertId}' for i in items], project_id=pid)
        info.asOf = rows[0].read_at
        return Result[Page](data=Page(items=items, page=page, pageSize=size, total=rows[0].total, asOf=info.asOf), meta=info)
    return run(auth,op,read)


@router.get('/projects/{projectId}/alerts',operation_id='pmo_alert_list',response_model_exclude_none=True)
def project_alerts(projectId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(100,ge=1,le=100))->Result[Page]:
    return listing(authorization,projectId,page,pageSize)


@router.get('/analytics/health-alerts',operation_id='human_alerts_overview',response_model_exclude_none=True)
def overview(authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100),
             ruleId:str|None=Query(None,pattern=r'^LR-0[1-8]$'),month:str|None=Query(None,pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])$'))->Result[Page]:
    return listing(authorization,None,page,pageSize,ruleId,month)
