"""Human workbench projection. A persisted sample is never a live health result."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import Field
import sqlalchemy as sa

from . import schema_v1 as db
from .access import Auth, DEEP_OBJECT_FILTERS, Filters
from .contracts import CharterSummary, ProjectView, Result, Stage, Strict
from .costs import DisplayedRate
from .dto import Proportion, Unavailable
from .gates import lock_project
from .health import current
from .health_trace import Color
from .identity import visible
from .projects import can_edit_draft, listing_filters, meta, read_charter_summary, run, view
from .reviews import transitions
from .rule_scan import latest_query

router = APIRouter(prefix='/api/v1', tags=['Project overview'])
OP = 'human_project_overview_list'
DETAIL = 'human_project_overview_get'


class Manager(Strict):
    personId: UUID
    name: str


class HealthSample(Strict):
    snapshotId: UUID
    asOf: datetime
    health: Literal['green', 'yellow', 'red', 'untrusted']
    publication: Literal['official', 'preview', 'withheld']
    ruleVersion: str


class OverviewRow(ProjectView):
    materialsVisible: bool
    canEditDraft: bool
    manager: Manager | None = None
    healthSample: HealthSample | None = None


class OverviewPage(Strict):
    items: list[OverviewRow]
    page: int
    pageSize: int
    total: int


class Indicators(Strict):
    asOf: datetime
    health: Color | Literal['untrusted', 'unavailable']
    publication: Literal['official', 'preview', 'withheld', 'unavailable']
    ruleVersion: str
    snapshotId: UUID | None = None
    timeLine: Color | None = None
    moneyVarianceRate: DisplayedRate | Unavailable | None = None
    effortVarianceRate: DisplayedRate | Unavailable | None = None
    requirementSatisfactionRate: Proportion | Unavailable | None = None


class StageReceipt(Strict):
    fromStage: Stage
    toStage: Stage
    fromRevision: int
    toRevision: int
    transitionedAt: datetime
    requestId: UUID
    reviewId: UUID | None = None
    approvalRequestId: UUID | None = None


class OverviewDetail(Strict):
    project: ProjectView
    materialsVisible: bool
    charterSummary: CharterSummary | None = None
    indicators: Indicators | None = None
    recentTransitions: list[StageReceipt] = Field(max_length=5)


def page_query(actor, filters, page, size):
    p = db.projects
    authorized = sa.select(p, visible(actor).label('materials_visible')).where(
        *listing_filters(actor, filters.stage, filters.keyword)).cte('authorized_projects')
    counts = sa.select(sa.func.count().label('total')).select_from(authorized).subquery()
    selected = sa.select(authorized).order_by(authorized.c.created_at.desc(), authorized.c.id).limit(size).offset(
        min((page - 1) * size, 2**63 - 1)).subquery('overview_page')
    at = sa.func.statement_timestamp()

    m, n, u = db.project_members.c, db.persons.c, db.user_accounts.c
    manager = (sa.select(n.id.label('manager_id'), n.name.label('manager_name'), n.revision.label('manager_revision'))
        .select_from(db.project_members.join(db.persons, n.id == m.person_id).join(db.user_accounts, u.id == n.user_id))
        .where(selected.c.materials_visible, m.project_id == selected.c.id, n.id == selected.c.pm_id,
               n.org_id == selected.c.org_id, m.role == 'pm', m.assignment_status == 'accepted',
               m.accepted_at.is_not(None), m.revoked_at.is_(None), n.is_active, u.is_active)
        .correlate(selected).lateral('accepted_pm'))
    s = db.health_snapshots.c
    sample = (latest_query(selected.c.id, at).with_only_columns(s.id.label('snapshot_id'), s.as_of,
        s.results['health'].astext.label('sample_health'), s.results['publication'].astext.label('sample_publication'),
        s.rule_version).where(selected.c.materials_visible).correlate(selected).lateral('latest_sample'))

    # The count and page share a SQL snapshot. Only the selected page requests PM and sample projections.
    return (sa.select(selected, counts.c.total, at.label('read_at'), manager, sample)
            .select_from(counts.outerjoin(selected, sa.true()).outerjoin(manager, sa.true()).outerjoin(sample, sa.true()))
            .order_by(selected.c.created_at.desc(), selected.c.id))


@router.get('/project-overview', operation_id=OP, response_model_exclude_none=True, openapi_extra=DEEP_OBJECT_FILTERS)
def listing(authorization: Auth, filters: Filters, page: int = Query(1, ge=1),
            pageSize: int = Query(20, ge=1, le=100)) -> Result[OverviewPage]:
    def read(c, a, _):
        rows = c.execute(page_query(a, filters, page, pageSize)).mappings().all()
        items: list[OverviewRow] = []
        refs: list[str] = []
        for row in rows:
            if row.id is None:
                continue
            refs.append(f'project:{row.id}@{row.revision}')
            manager = sample = None
            if row.manager_id is not None:
                manager = Manager(personId=row.manager_id, name=row.manager_name)
                refs.append(f'person:{row.manager_id}@{row.manager_revision}')
            if row.snapshot_id is not None:
                sample = HealthSample(snapshotId=row.snapshot_id, asOf=row.as_of, health=row.sample_health,
                                      publication=row.sample_publication, ruleVersion=row.rule_version)
                refs.append(f'health-snapshot:{row.snapshot_id}')
            items.append(OverviewRow(**view(row).model_dump(), materialsVisible=row.materials_visible,
                                     canEditDraft=can_edit_draft(a, row), manager=manager, healthSample=sample))
        info = meta(OP, list(dict.fromkeys(refs)))
        info.asOf = rows[0].read_at
        return Result[OverviewPage](data=OverviewPage(items=items, page=page, pageSize=pageSize, total=rows[0].total), meta=info)
    return run(authorization, OP, read)


@router.get('/project-overview/{projectId}', operation_id=DETAIL, response_model_exclude_none=True)
def detail(projectId: UUID, authorization: Auth) -> Result[OverviewDetail]:
    def read(c, a, _):
        project = lock_project(c, a, projectId)
        as_of = c.scalar(sa.select(sa.func.clock_timestamp()))
        result = OverviewDetail(project=view(project), materialsVisible=project.materials_visible, recentTransitions=[])
        refs = [f'project:{project.id}@{project.revision}']
        if project.materials_visible:
            result.charterSummary = charter = read_charter_summary(c, project.id)
            if charter:
                refs.append(f'charter:{charter.charterId}@{charter.revision}')
            observation = current(c, a, project, as_of, human=False)
            result.indicators = Indicators.model_validate(observation.model_dump(include=set(Indicators.model_fields)))
            refs.extend(observation.sourceRefs)
            t = transitions.c
            rows = c.execute(sa.select(t.from_stage, t.to_stage, t.from_revision, t.to_revision, t.transitioned_at,
                                       t.request_id, t.review_id, t.approval_request_id)
                             .where(t.project_id == project.id).order_by(t.from_revision.desc()).limit(5)).mappings()
            for row in rows:
                result.recentTransitions.append(StageReceipt(fromStage=row.from_stage, toStage=row.to_stage,
                    fromRevision=row.from_revision, toRevision=row.to_revision, transitionedAt=row.transitioned_at,
                    requestId=row.request_id, reviewId=row.review_id, approvalRequestId=row.approval_request_id))
                refs.append(f'project-transition:{project.id}@{row.to_revision}')
                refs.append(f'review:{row.review_id}' if row.review_id else f'approval:{row.approval_request_id}')
        info = meta(DETAIL, list(dict.fromkeys(refs)), project_id=project.id)
        info.asOf = as_of
        return Result[OverviewDetail](data=result, meta=info)
    return run(authorization, DETAIL, read)
