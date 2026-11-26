"""Append independent, effective-dated classification of legacy verified postings."""
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .category_costs import HUMAN, classifications, clock, locked_project
from .contracts import Result, Strict
from .cost_categories import CATEGORY_KIND, Category
from .identity import Failure
from .materials import finish, replay
from .policy import freeze, matches, require as require_policy
from .postings import OPEN, finance
from .projects import fingerprint, meta, run
from .requirement_baselines import active_member
from .requirement_delivery import can_deliver

router = APIRouter(prefix='/api/v1', tags=['Cost postings'])
PROPOSE = 'human_cost_classification_propose'
VERIFY = 'human_cost_classification_verify'
PATH = '/projects/{projectId}/cost-postings/{postingId}/classification'
requests = sa.table('category_posting_requests', sa.column('id', sa.Uuid), sa.column('project_id', sa.Uuid),
    sa.column('posting_id', sa.Uuid), sa.column('category'), sa.column('reason'), sa.column('created_by', sa.Uuid),
    sa.column('expected_classification', sa.Uuid), sa.column('created_at', sa.DateTime(timezone=True)))
verified = sa.table('category_posting_classifications', sa.column('id', sa.Uuid), sa.column('project_id', sa.Uuid),
    sa.column('posting_id', sa.Uuid), sa.column('request_id', sa.Uuid), sa.column('verified_by', sa.Uuid),
    sa.column('rationale'), sa.column('effective_at', sa.DateTime(timezone=True)))


class Reason(Strict):
    reason: str = Field(min_length=3, max_length=2000, pattern=r'\S')


class Proposal(Reason):
    category: Category


class Receipt(Strict):
    requestId: UUID
    postingId: UUID
    category: Category
    submittedAt: datetime
    classificationId: UUID | None = None
    effectiveAt: datetime | None = None


def current_categories(pid):
    return sa.select(verified.c.posting_id, verified.c.id, requests.c.category).select_from(
        verified.join(requests, requests.c.id == verified.c.request_id)).where(verified.c.project_id == pid).distinct(
        verified.c.posting_id).order_by(verified.c.posting_id, verified.c.effective_at.desc(), verified.c.id.desc()).subquery()


def pending(c, pid, posting_ids):
    r = requests.c
    current = current_categories(pid)
    rows = c.execute(sa.select(requests).select_from(requests.outerjoin(current, current.c.posting_id == r.posting_id))
        .where(r.project_id == pid, r.posting_id.in_(posting_ids), r.expected_classification.is_not_distinct_from(current.c.id),
            ~sa.exists(sa.select(verified.c.id).where(verified.c.request_id == r.id)))
        .distinct(r.posting_id).order_by(r.posting_id, r.created_at.desc(), r.id.desc())).mappings()
    return {row.posting_id: row for row in rows}


def posting_for_classification(c, p, pid):
    t, b = db.cost_postings.c, db.cost_entries.c
    row = c.execute(sa.select(db.cost_postings, b.category).join(db.cost_entries, b.id == t.cost_entry_id)
        .where(t.id == pid, t.project_id == p.id)).mappings().one_or_none()
    if row is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND', project_id=p.id)
    if row.status != 'verified' or row.posting_of_id or row.category in CATEGORY_KIND:
        raise Failure(422, 'CLASSIFICATION_REQUIRES_LEGACY_POSTING', project_id=p.id)
    return row


def receipt(c, a, p, request, use, key, rid, sig, approval=None):
    result = Receipt(requestId=request.id, postingId=request.posting_id, category=request.category,
        submittedAt=request.created_at, classificationId=approval.id if approval else None,
        effectiveAt=approval.effective_at if approval else None)
    refs = [f'cost-classification-request:{request.id}', f'cost-posting:{request.posting_id}@2']
    if approval is not None:
        refs.append(f'cost-classification:{approval.id}')
    return finish(c, a, use, key, rid, p.id, sig, Result[Receipt](data=result, meta=meta(use, refs)))


@router.post(PATH, operation_id=PROPOSE, response_model_exclude_none=True, openapi_extra=HUMAN)
def propose(projectId: UUID, postingId: UUID, command: Proposal, authorization: Auth, key: Key,
            expected: Revision) -> Result[Receipt]:
    rev = int(expected)
    sig = fingerprint([str(projectId), str(postingId), rev, command.model_dump()])

    def write(c, a, rid):
        p = locked_project(c, a, projectId)
        if not can_deliver(c, a, p):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        old = replay(c, a, PROPOSE, key, sig, Result[Receipt])
        if old:
            return old
        if p.stage not in OPEN:
            raise Failure(422, 'LEDGER_LOCKED', project_id=p.id)
        row = posting_for_classification(c, p, postingId)
        if rev != 2:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if CATEGORY_KIND[command.category] != row.type:
            raise Failure(422, 'INVALID_CATEGORY_UNIT', project_id=p.id)
        reason = command.reason.strip()
        if len(reason) < 3:
            raise Failure(422, 'INVALID_REASON', project_id=p.id)
        current = classifications(c, p.id, clock(c)).get(row.id)
        if current and current.category == command.category:
            raise Failure(422, 'NO_CHANGE', project_id=p.id)
        request = c.execute(requests.insert().values(project_id=p.id, posting_id=row.id, created_by=a.person,
            category=command.category, reason=reason, expected_classification=current.id if current else None)
            .returning(*requests.c)).mappings().one()
        freeze(c, p, 'cost.category', request.id)
        return receipt(c, a, p, request, PROPOSE, key, rid, sig)

    return run(authorization, PROPOSE, write)


@router.post(PATH + '/{requestId}/verify', operation_id=VERIFY, response_model_exclude_none=True, openapi_extra=HUMAN)
def verify(projectId: UUID, postingId: UUID, requestId: UUID, command: Reason, authorization: Auth, key: Key,
           expected: Revision) -> Result[Receipt]:
    rev = int(expected)
    sig = fingerprint([str(projectId), str(postingId), str(requestId), rev, command.model_dump()])

    def write(c, a, rid):
        p = locked_project(c, a, projectId)
        if not finance(c, a):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        row = posting_for_classification(c, p, postingId)
        r = requests.c
        request = c.execute(sa.select(requests).where(r.id == requestId, r.project_id == p.id,
                                                      r.posting_id == row.id)).mappings().one_or_none()
        if request is None:
            raise Failure(404, 'RESOURCE_NOT_FOUND', project_id=p.id)
        if a.person in (request.created_by, row.created_by):
            raise Failure(403, 'INDEPENDENT_VERIFIER_REQUIRED', project_id=p.id)
        old = replay(c, a, VERIFY, key, sig, Result[Receipt])
        if old:
            return old
        if p.stage not in OPEN:
            raise Failure(422, 'LEDGER_LOCKED', project_id=p.id)
        if rev != 2:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if not active_member(c, p, request.created_by, 'pm'):
            raise Failure(409, 'CLASSIFICATION_REQUEST_STALE', project_id=p.id)
        current = classifications(c, p.id, clock(c)).get(row.id)
        if (current.id if current else None) != request.expected_classification:
            raise Failure(409, 'CLASSIFICATION_REQUEST_STALE', project_id=p.id)
        reason = command.reason.strip()
        if len(reason) < 3:
            raise Failure(422, 'INVALID_REASON', project_id=p.id)
        require_policy(c, p, 'cost.category', rid)
        if not matches(c, p, 'cost.category', request.id):
            raise Failure(409, 'POLICY_SNAPSHOT_STALE', project_id=p.id)
        approval = c.execute(verified.insert().values(project_id=p.id, posting_id=row.id, request_id=request.id,
            verified_by=a.person, rationale=reason).returning(*verified.c)).mappings().one()
        return receipt(c, a, p, request, VERIFY, key, rid, sig, approval)

    return run(authorization, VERIFY, write)
