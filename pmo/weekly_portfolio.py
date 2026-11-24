"""Formally published weekly reports within the caller's project and report scope."""
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from . import reports as report_service
from .access import Auth
from .contracts import Result, Strict
from .identity import Failure, visible
from .pagination import pg_offset
from .projects import meta, run

router = APIRouter(tags=['Reports'])
OP = 'human_weekly_portfolio_report_list'
p, r = report_service.projects.c, report_service.r


class Entry(Strict):
    projectId: UUID
    projectCode: str
    projectName: str
    status: Literal['published', 'no_readable_publication', 'source_unavailable']
    reportId: UUID | None = None
    revision: int | None = None
    versionNo: int | None = None
    publishedAt: datetime | None = None
    sourceRef: str | None = None
    milestoneClaims: int | None = None
    requirementClaims: int | None = None
    riskClaims: int | None = None
    declaredPersonDays: str | None = None


class WeekPage(Strict):
    weekStart: date
    weekEnd: date
    period: str
    asOf: datetime
    page: int
    pageSize: int
    total: int
    items: list[Entry]


def entry(row, as_of: datetime) -> Entry:
    base = {'projectId': row.scoped_id, 'projectCode': row.scoped_code, 'projectName': row.scoped_name}
    if row.id is None:
        return Entry(**base, status='no_readable_publication')
    fields = report_service.publication_fields(row, as_of)
    if fields is None:
        return Entry(**base, status='source_unavailable')
    return Entry(**base, status='published', reportId=row.id, revision=row.revision, versionNo=row.version_no,
                 publishedAt=row.confirmed_at, sourceRef=report_service._ref(row),
                 milestoneClaims=len(fields.milestone_progress), requirementClaims=len(fields.requirement_progress),
                 riskClaims=len(fields.risks_and_blockers), declaredPersonDays=fields.effort_person_days)


@router.get('/weekly-reports', operation_id=OP, response_model_exclude_none=True, openapi_extra=report_service.HUMAN)
def weekly_reports(authorization: Auth, weekStart: date | None = None, projectId: UUID | None = None,
                   asOf: AwareDatetime | None = None, page: int = Query(1, ge=1),
                   pageSize: int = Query(20, ge=1, le=50)) -> Result[WeekPage]:
    def read(c, actor, _):
        if actor.kind != 'human':
            raise Failure(403, 'FORBIDDEN')
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        at = asOf or now
        if at > now:
            raise Failure(422, 'FUTURE_AS_OF')
        today = at.astimezone(report_service.TZ).date()
        start = weekStart or today - timedelta(days=today.weekday())
        if start.weekday() != 0 or start > today:
            raise Failure(422, 'INVALID_REPORT_WEEK')
        iso = start.isocalendar()
        period = f'{iso.year:04d}-W{iso.week:02d}'
        scope = [visible(actor)]
        if projectId is not None:
            scope.append(p.id == projectId)
        total = c.scalar(sa.select(sa.func.count()).select_from(report_service.projects).where(*scope))
        if projectId is not None and total == 0:
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        # Resolve the publication before report ACLs so an unreadable latest
        # publication never falls back to an older version the caller authored.
        candidate = report_service.reports.alias().c
        latest = (sa.select(candidate.id).where(candidate.project_id == p.id, candidate.type == 'weekly',
            candidate.period == period, candidate.status == 'confirmed', candidate.confirmed_at <= at,
            candidate.confirmed_by.is_not(None), candidate.submitted_by.is_not(None),
            candidate.submitted_at <= candidate.confirmed_at, candidate.content_hash.is_not(None), candidate.revision >= 2)
            .order_by(candidate.version_no.desc(), candidate.id).limit(1)
            .correlate(report_service.projects).scalar_subquery())
        publications_visible = sa.and_(r.id == latest, report_service.report_read_scope(actor))
        stmt = (sa.select(p.id.label('scoped_id'), p.code.label('scoped_code'), p.name.label('scoped_name'),
                          report_service.reports)
                .select_from(report_service.projects.outerjoin(report_service.reports, publications_visible))
                .where(*scope).order_by(p.code, p.id).limit(pageSize).offset(pg_offset(page, pageSize)))
        items = [entry(row, at) for row in c.execute(stmt).mappings()]
        info = meta(OP, [item.sourceRef for item in items if item.sourceRef is not None])
        info.asOf = at
        return Result[WeekPage](data=WeekPage(weekStart=start, weekEnd=start + timedelta(days=6), period=period,
            asOf=at, page=page, pageSize=pageSize, total=total, items=items), meta=info)
    return run(authorization, OP, read)
