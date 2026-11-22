"""Read-only inbox of actionable gate reviews and stage decisions."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import Field

from . import project_lifecycle as lifecycle, review_outcomes as outcomes, schema_v1 as db
from .access import Auth
from .contracts import RULE_VERSION, ProjectView, Result, Stage, Strict
from .gates import REVIEW, Gate, lock_project
from .identity import Failure, visible
from .projects import meta, run, view
from .requirement_delivery import synthetic_confirmation
from .reviews import APPROVAL_ACTION

OP = 'human_approval_queue'
router = APIRouter(prefix='/api/v1', tags=['Personal approvals'])
ReviewAction = Literal['score', 'decide', 'confirm_sponsor', 'confirm_initiator']


class PendingReview(Strict):
    kind: Literal['review'] = 'review'
    project: ProjectView
    reviewId: UUID
    reviewType: Gate
    revision: int
    submittedAt: datetime
    actions: list[ReviewAction] = Field(min_length=1, max_length=4)


class PendingTransition(Strict):
    kind: Literal['transition'] = 'transition'
    project: ProjectView
    approvalId: UUID
    fromStage: Stage
    toStage: Stage
    revision: int
    submittedAt: datetime
    awaitsInitiator: bool
    actions: list[Literal['approve_transition', 'reject_transition']] = Field(min_length=1, max_length=2)


Pending = Annotated[PendingReview | PendingTransition, Field(discriminator='kind')]


class QueuePage(Strict):
    items: list[Pending] = Field(max_length=100)
    page: int
    pageSize: int
    total: int


def candidates(a, allow_stage_decisions):
    p, q, r, i = db.projects, db.approval_requests, db.reviews, lifecycle.intents
    m, g = db.project_members.c, lifecycle.transition_grants.c
    # These are necessary conditions only. The original services make the eligibility decision.
    reviewer = sa.exists(sa.select(m.id).where(m.project_id == p.c.id, m.person_id == a.person, m.role == 'reviewer',
        m.assignment_status == 'accepted', m.accepted_at.is_not(None), m.revoked_at.is_(None)))
    scope = (visible(a, include_initiator=True), q.c.status == 'pending', q.c.rule_version == RULE_VERSION)
    gates = sa.select(q.c.id, q.c.project_id, q.c.created_at, sa.literal('review').label('kind')).select_from(
        q.join(p, p.c.id == q.c.project_id).join(r, sa.and_(r.c.id == q.c.id, r.c.project_id == p.c.id))).where(
        *scope, q.c.action == APPROVAL_ACTION, r.c.status == 'pending',
        sa.tuple_(r.c.type, p.c.stage).in_(tuple(REVIEW.items())),
        sa.or_(reviewer, p.c.initiator_id == a.person, p.c.sponsor_id == a.person))
    if not allow_stage_decisions:
        return gates.order_by(q.c.created_at.desc(), q.c.id)

    delegated = sa.exists(sa.select(g.id).where(g.org_id == a.org, g.project_id == p.c.id, g.person_id == a.person,
        g.from_stage == i.c.from_stage, g.to_stage == i.c.to_stage, g.revoked_at.is_(None),
        g.valid_from <= sa.func.statement_timestamp(), g.expires_at > sa.func.statement_timestamp()))
    stages = sa.select(q.c.id, q.c.project_id, q.c.created_at, sa.literal('transition')).select_from(
        q.join(p, p.c.id == q.c.project_id).join(i, i.c.approval_id == q.c.id)).where(
        *scope, q.c.action == lifecycle.ACTION, p.c.stage == i.c.from_stage, q.c.expected_revision == p.c.revision,
        sa.tuple_(i.c.from_stage, i.c.to_stage).in_(sorted(lifecycle.APPROVED)), q.c.requested_by != a.person,
        q.c.delegated_by.is_distinct_from(a.person),
        sa.or_(p.c.sponsor_id == a.person, sa.and_(i.c.to_stage == 'archived', reviewer), delegated))
    combined = sa.union_all(gates, stages).subquery()
    return sa.select(combined).order_by(combined.c.created_at.desc(), combined.c.id)


def review_entry(c, a, candidate) -> PendingReview | None:
    p, review, frozen = outcomes.context(c, a, candidate.id)
    state = outcomes.workbench(c, a, p, review, frozen)
    options: tuple[tuple[ReviewAction, bool], ...] = (('score', state.canScore), ('decide', state.canDecide),
        ('confirm_sponsor', state.canConfirmSponsor), ('confirm_initiator', state.canConfirmInitiator))
    actions = [action for action, allowed in options if allowed]
    if not actions:
        return None
    return PendingReview(project=view(p), reviewId=review.id, reviewType=review.type, revision=review.revision,
                         submittedAt=candidate.created_at, actions=actions)


def stage_entry(c, a, candidate) -> PendingTransition | None:
    p = lock_project(c, a, candidate.project_id)
    req, intent = lifecycle.lookup(c, p.id, candidate.id)
    item = lifecycle.entry(c, a, p, req, intent)
    if not item.canDecide:
        return None
    return PendingTransition(project=view(p), approvalId=req.id, fromStage=intent.from_stage, toStage=intent.to_stage,
        revision=item.revision, submittedAt=item.requestedAt, awaitsInitiator=item.awaitsInitiator,
        actions=['reject_transition'] if item.awaitsInitiator else ['approve_transition', 'reject_transition'])


@router.get('/me/approval-queue', operation_id=OP, response_model_exclude_none=True)
def queue(authorization: Auth, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100)) -> Result[QueuePage]:
    def read(c, a, _):
        matches: list[Pending] = []
        total = 0
        offset = (page - 1) * pageSize
        for candidate in c.execute(candidates(a, synthetic_confirmation(c))).mappings().all():
            try:
                item = review_entry(c, a, candidate) if candidate.kind == 'review' else stage_entry(c, a, candidate)
            except Failure as error:
                # Access may have changed since candidate selection. Never disclose the missing resource.
                if error.status != 404 or error.code != 'RESOURCE_NOT_FOUND':
                    raise
                continue
            if item is not None:
                if offset <= total < offset + pageSize:
                    matches.append(item)
                total += 1
        info = meta(OP, [f'review:{item.reviewId}@{item.revision}' if isinstance(item, PendingReview)
                         else f'approval:{item.approvalId}' for item in matches])
        return Result[QueuePage](data=QueuePage(items=matches, page=page, pageSize=pageSize, total=total), meta=info)
    return run(authorization, OP, read)
