"""Formal objections from the authenticated customer, never inferred from reports."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import StringConstraints

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .pagination import pg_offset
from .contracts import Result, Strict
from .identity import Failure, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_assignment
from .policy import enabled as policy_enabled, guard, require
from .requirement_delivery import baseline_evidence_time

LIST = 'human_requirement_disputes_list'
OPEN = 'human_requirement_dispute_open'
WITHDRAW = 'human_requirement_dispute_withdraw'
PATH = '/requirements/{requirementId}/disputes'
STAGES = ('executing', 'on_hold', 'delivering', 'closing_review')
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
router = APIRouter(prefix='/api/v1', tags=['Customer requirement disputes'])

t = sa.table('requirement_disputes',
    *(sa.column(k, sa.Uuid) for k in ('id', 'project_id', 'requirement_id', 'customer_id', 'assignment_id',
                                       'stakeholder_id', 'open_request_id', 'withdrawal_request_id')),
    sa.column('baseline_version', sa.Integer), sa.column('requirement_revision', sa.BigInteger),
    sa.column('revision', sa.BigInteger), sa.column('reason'), sa.column('withdrawal_reason'),
    sa.column('opened_at', sa.DateTime(timezone=True)), sa.column('withdrawn_at', sa.DateTime(timezone=True)))


class Statement(Strict):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=2000,
                                             pattern=r'^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+$')]


class Dispute(Strict):
    disputeId: UUID
    requirementId: UUID
    requirementRevision: int
    baselineVersion: int
    revision: int
    customerId: UUID
    customerStakeholderId: UUID
    reason: str
    openedAt: datetime
    withdrawnAt: datetime | None = None
    withdrawalReason: str | None = None


class Page(Strict):
    projectId: UUID
    requirementId: UUID
    requirementRevision: int
    baselineVersion: int
    canOpen: bool
    withdrawableIds: list[UUID]
    page: int
    pageSize: int
    total: int
    items: list[Dispute]


def source(row):
    return f'requirement-dispute:{row.id}@{row.revision}'


def effective(c, pid, *, open_only=False):
    r = db.requirements.c
    query = sa.select(t).join(db.requirements, sa.and_(r.id == t.c.requirement_id,
        r.project_id == t.c.project_id, r.baseline_version == t.c.baseline_version)).where(
            t.c.project_id == pid, r.status != 'cancelled')
    if open_only:
        query = query.where(t.c.withdrawn_at.is_(None))
    return c.execute(query.order_by(t.c.id)).mappings().all()


def open_query(row):
    return sa.select(t.c.id).where(t.c.project_id == row.project_id, t.c.requirement_id == row.id,
                                  t.c.baseline_version == row.baseline_version, t.c.withdrawn_at.is_(None))


def _get(c, a, req_id, *, write):
    p, r = db.projects, db.requirements
    project = c.execute(sa.select(p).join(r, r.c.project_id == p.c.id).where(r.c.id == req_id, visible(a))
                        .with_for_update(read=not write, of=p)).mappings().one_or_none()
    if project is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return project, c.execute(sa.select(r).where(r.c.id == req_id)).mappings().one()


def _authority(c, a, p, r, *, stakeholder=None):
    if (a.kind != 'human' or a.person in (p.pm_id, p.sponsor_id, p.initiator_id, r.created_by, r.accepted_by)
            or active_assignment(c, p, a.person, 'reviewer')):
        return None
    accepted = active_assignment(c, p, a.person, 'client_representative')
    if accepted is None:
        return None
    s = db.stakeholders.c
    query = sa.select(s.id).where(s.project_id == p.id, s.type == 'customer', s.linked_person_id == a.person)
    if stakeholder is not None:
        query = query.where(s.id == stakeholder)
    sid = c.scalar(query.order_by(s.id).limit(1).with_for_update(read=True))
    return (accepted.id, sid) if sid is not None else None


def _view(row):
    return Dispute(disputeId=row.id, requirementId=row.requirement_id, baselineVersion=row.baseline_version,
        requirementRevision=row.requirement_revision, revision=row.revision, customerId=row.customer_id,
        customerStakeholderId=row.stakeholder_id, reason=row.reason, openedAt=row.opened_at,
        withdrawnAt=row.withdrawn_at, withdrawalReason=row.withdrawal_reason)


def _finish(c, a, p, row, op, key, rid, sig, before=None):
    refs = [source(row), f'requirement:{row.requirement_id}@{row.requirement_revision}']
    out = Result[Dispute](data=_view(row), meta=meta(op, refs))
    body = out.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, row.id, p.id, sig, body, fingerprint(body), before)
    c.execute(db.outbox_events.insert().values(event_type=op, dedupe_key=rid, status='pending',
        payload={'projectId': str(p.id), 'requirementId': str(row.requirement_id), 'disputeId': str(row.id),
                 'revision': row.revision}))
    return out


@router.get(PATH, operation_id=LIST, openapi_extra=HUMAN, response_model_exclude_none=True)
def listing(requirementId: UUID, authorization: Auth, page: int = Query(1, ge=1, le=10000),
            pageSize: int = Query(10, ge=1, le=50)) -> Result[Page]:
    def read(c, a, _):
        p, r = _get(c, a, requirementId, write=False)
        filters = (t.c.project_id == p.id, t.c.requirement_id == r.id)
        total = c.scalar(sa.select(sa.func.count()).select_from(t).where(*filters))
        rows = c.execute(sa.select(t).where(*filters).order_by(t.c.opened_at.desc(), t.c.id.desc())
                         .limit(pageSize).offset(pg_offset(page,pageSize))).mappings().all()
        enabled = p.stage in STAGES and policy_enabled(c, p, 'requirement.dispute')
        can_open = bool(enabled and r.baseline_version and r.status != 'cancelled' and _authority(c, a, p, r)
                        and not c.scalar(open_query(r).where(t.c.customer_id == a.person).limit(1)))
        withdrawable = [row.id for row in rows if enabled and row.customer_id == a.person and row.withdrawn_at is None
                        and _authority(c, a, p, r, stakeholder=row.stakeholder_id) == (row.assignment_id, row.stakeholder_id)]
        return Result[Page](data=Page(projectId=p.id, requirementId=r.id, requirementRevision=r.revision,
            baselineVersion=r.baseline_version, canOpen=can_open, withdrawableIds=withdrawable, page=page, pageSize=pageSize,
            total=total, items=[_view(row) for row in rows]), meta=meta(LIST, [f'requirement:{r.id}@{r.revision}',
                                                                            *(source(row) for row in rows)]))
    return run(authorization, LIST, read)


@router.post(PATH, operation_id=OPEN, openapi_extra=HUMAN, response_model_exclude_none=True)
def open_dispute(requirementId: UUID, command: Statement, authorization: Auth, key: Key, expected: Revision) -> Result[Dispute]:
    rev = int(expected)
    sig = fingerprint([str(requirementId), rev, command.reason])

    def write(c, a, rid):
        p, r = _get(c, a, requirementId, write=True)
        authority = _authority(c, a, p, r)
        if authority is None:
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        guard(c, p, 'requirement.dispute')
        prior = replay(c, a, OPEN, key, sig, Result[Dispute])
        if prior:
            return prior
        require(c, p, 'requirement.dispute', rid)
        if p.stage not in STAGES or not r.baseline_version or r.status == 'cancelled':
            raise Failure(422, 'REQUIREMENT_DISPUTE_LOCKED', project_id=p.id)
        if r.revision != rev:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        baseline_evidence_time(c, p.id, r)
        if c.scalar(open_query(r).where(t.c.customer_id == a.person).limit(1)):
            raise Failure(409, 'CUSTOMER_DISPUTE_OPEN', project_id=p.id)
        row = c.execute(t.insert().values(project_id=p.id, requirement_id=r.id, baseline_version=r.baseline_version,
            requirement_revision=rev, customer_id=a.person, assignment_id=authority[0], stakeholder_id=authority[1],
            reason=command.reason, open_request_id=UUID(rid)).returning(t)).mappings().one()
        return _finish(c, a, p, row, OPEN, key, rid, sig)
    return run(authorization, OPEN, write)


@router.post(PATH + '/{disputeId}/withdraw', operation_id=WITHDRAW, openapi_extra=HUMAN, response_model_exclude_none=True)
def withdraw(requirementId: UUID, disputeId: UUID, command: Statement, authorization: Auth,
             key: Key, expected: Revision) -> Result[Dispute]:
    rev = int(expected)
    sig = fingerprint([str(requirementId), str(disputeId), rev, command.reason])

    def write(c, a, rid):
        p, r = _get(c, a, requirementId, write=True)
        old = c.execute(sa.select(t).where(t.c.id == disputeId, t.c.project_id == p.id,
                                          t.c.requirement_id == r.id)).mappings().one_or_none()
        if old is None:
            raise Failure(404, 'RESOURCE_NOT_FOUND', project_id=p.id)
        if (old.customer_id != a.person or _authority(c, a, p, r, stakeholder=old.stakeholder_id)
                != (old.assignment_id, old.stakeholder_id)):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        guard(c, p, 'requirement.dispute')
        prior = replay(c, a, WITHDRAW, key, sig, Result[Dispute])
        if prior:
            return prior
        require(c, p, 'requirement.dispute', rid)
        if p.stage not in STAGES:
            raise Failure(422, 'REQUIREMENT_DISPUTE_LOCKED', project_id=p.id)
        if old.revision != rev:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if old.withdrawn_at is not None:
            raise Failure(422, 'DISPUTE_ALREADY_WITHDRAWN', project_id=p.id)
        row = c.execute(t.update().where(t.c.id == old.id).values(revision=rev + 1, withdrawn_at=sa.func.clock_timestamp(),
            withdrawal_reason=command.reason, withdrawal_request_id=UUID(rid)).returning(t)).mappings().one()
        return _finish(c, a, p, row, WITHDRAW, key, rid, sig, fingerprint(_view(old).model_dump(mode='json')))
    return run(authorization, WITHDRAW, write)
