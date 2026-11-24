"""Human workbench projection. A persisted sample is never a live health result."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import Field
import sqlalchemy as sa

from . import schema_v1 as db
from .access import Auth, DEEP_OBJECT_FILTERS, Filters
from .change_requests import ledger_scope
from .contracts import CharterSummary, ProjectView, Result, Stage, Strict
from .costs import DisplayedRate
from .dto import Proportion, Unavailable
from .gates import lock_project
from .health import current
from .health_trace import Color
from .identity import visible
from .projects import can_edit_draft, listing_filters, meta, read_charter_summary, run, view
from .reviews import transitions
from .rule_scan import feasibility_overdue, latest_query

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


ActivityKind = Literal['risk_created', 'change_requested', 'change_applied', 'change_rejected']


class Activity(Strict):
    kind: ActivityKind
    resourceId: UUID
    occurredAt: datetime


class OverdueCondition(Strict):
    taskId: UUID
    title: str
    dueAt: datetime


class RuleNotification(Strict):
    alertId: UUID
    ruleId: Literal['LR-04', 'LR-08']
    status: Literal['open', 'resolved']
    deliveredAt: datetime


class OverviewDetail(Strict):
    project: ProjectView
    materialsVisible: bool
    charterSummary: CharterSummary | None = None
    indicators: Indicators | None = None
    recentTransitions: list[StageReceipt] = Field(max_length=5)
    recentActivity: list[Activity] | None = Field(default=None, max_length=5)
    overdueConditions: list[OverdueCondition] | None = Field(default=None, max_length=5)
    overdueConditionCount: int | None = None
    ruleNotifications: list[RuleNotification] | None = Field(default=None, max_length=5)


def personal_rule_notifications(c, p, a, as_of):
    n, e = db.notifications, db.rule_events
    rows = c.execute(sa.select(e.c.id, e.c.rule_id, e.c.status, n.c.delivered_at)
        .join(n, sa.and_(n.c.rule_event_id == e.c.id, n.c.project_id == e.c.project_id))
        .where(n.c.project_id == p.id, n.c.recipient_person_id == a.person, n.c.delivery_status == 'delivered',
               n.c.delivered_at <= as_of, e.c.rule_id.in_(('LR-04', 'LR-08')))
        .order_by(n.c.delivered_at.desc(), e.c.id).limit(5)).mappings()
    return [RuleNotification(alertId=row.id, ruleId=row.rule_id, status=row.status, deliveredAt=row.delivered_at)
            for row in rows]


def recent_activity(c, project_id, as_of) -> list[Activity]:
    r, ch = db.risks.c, db.change_entries.c
    receipts = sa.table('change_decision_records', sa.column('change_id', sa.Uuid),
                        sa.column('outcome'), sa.column('decided_at', sa.DateTime(timezone=True)))
    d = receipts.c
    scope = ledger_scope(project_id)
    risks = sa.select(sa.literal('risk_created').label('kind'), r.id.label('resource_id'),
                      r.created_at.label('occurred_at')).where(r.project_id == project_id)
    requests = sa.select(sa.literal('change_requested'), ch.id, ch.created_at).where(scope)
    decisions = sa.select(sa.case((d.outcome == 'applied', 'change_applied'), else_='change_rejected'), ch.id,
                          d.decided_at).select_from(db.change_entries.join(receipts, d.change_id == ch.id)).where(
                              scope, d.outcome.in_(('applied', 'rejected')))
    events = sa.union_all(risks, requests, decisions).subquery()
    rows = c.execute(sa.select(events).where(events.c.occurred_at <= as_of).order_by(
        events.c.occurred_at.desc(), events.c.resource_id, events.c.kind).limit(5)).mappings()
    return [Activity(kind=r.kind, resourceId=r.resource_id, occurredAt=r.occurred_at) for r in rows]


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
        if a.kind == 'human':
            result.ruleNotifications = personal_rule_notifications(c, project, a, as_of) or None
        if project.materials_visible:
            result.charterSummary = charter = read_charter_summary(c, project.id)
            if charter:
                refs.append(f'charter:{charter.charterId}@{charter.revision}')
            observation = current(c, a, project, as_of, human=False)
            result.indicators = Indicators.model_validate(observation.model_dump(include=set(Indicators.model_fields)))
            refs.extend(observation.sourceRefs)
            overdue = feasibility_overdue(c, project, as_of)
            if overdue:
                result.overdueConditionCount = len(overdue)
                result.overdueConditions = [OverdueCondition(taskId=row.id, title=row.title, dueAt=row.due_at)
                                            for row in overdue[:5]]
                for row in overdue[:5]:
                    refs.extend([f'task:{row.id}@{row.revision}', f'review:{row.review_id}@{row.review_revision}'])
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
            result.recentActivity = recent_activity(c, project.id, as_of)
            refs.extend(f'{"risk" if item.kind == "risk_created" else "change"}:{item.resourceId}' for item in result.recentActivity)
        info = meta(DETAIL, list(dict.fromkeys(refs)), project_id=project.id)
        info.asOf = as_of
        return Result[OverviewDetail](data=result, meta=info)
    return run(authorization, DETAIL, read)
