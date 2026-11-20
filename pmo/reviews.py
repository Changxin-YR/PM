from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from . import schema_v1 as db
from .contracts import RULE_VERSION, Result, Strict
from .gates import ENTRY, REVIEW, Check, Gate, evaluate, lock_project, submitter
from .identity import Failure
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run

SUBMIT = 'pmo_review_submit'
LIST = 'pmo_review_list'
PREPARE = 'pmo_review_prepare'
CHECK = 'pmo_gate_check'
PAGE_SIZE = 100
reviews = db.reviews
snapshots = sa.table('review_submission_snapshots', sa.column('review_id', sa.Uuid), sa.column('rule_version'),
                     sa.column('snapshot_hash'), sa.column('materials', JSONB), sa.column('source_refs', JSONB))
transitions = sa.table('project_stage_transitions', sa.column('project_id', sa.Uuid), sa.column('from_revision'),
                       sa.column('to_revision'), sa.column('from_stage'), sa.column('to_stage'), sa.column('review_id', sa.Uuid),
                       sa.column('actor_id', sa.Uuid), sa.column('actor_type'), sa.column('request_id', sa.Uuid))


class Submit(Strict):
    reviewType: Gate


class ReviewView(Strict):
    reviewId: UUID
    projectId: UUID
    type: Gate
    status: Literal['draft', 'pending', 'decided']
    revision: int
    verdict: str | None = None


class ReviewPage(Strict):
    items: list[ReviewView]
    page: int
    pageSize: int
    total: int
    asOf: datetime


class Preparation(Strict):
    gateCheck: Check
    draftMaterials: list[str]


def view(row):
    return ReviewView(reviewId=row.id, projectId=row.project_id, type=row.type, status=row.status,
                      revision=row.revision, verdict=row.verdict)


def refs(project, materials):
    return list(dict.fromkeys([f'project:{project.id}@{project.revision}', *materials.refs]))


def preview(auth, pid, gate, *, prepare):
    op = PREPARE if prepare else CHECK

    def read(c, a, _):
        p = lock_project(c, a, pid)
        if not p.materials_visible and gate != 'charter':
            raise Failure(404, 'RESOURCE_NOT_FOUND')
        check, materials = evaluate(c, a, p, gate)
        sources = refs(p, materials)[:PAGE_SIZE]
        metadata = meta(op, sources)
        if prepare:
            can_submit = check.eligible and SUBMIT in a.actions and submitter(c, a, p, gate)
            result = Result[Preparation](data=Preparation(gateCheck=check, draftMaterials=sources[1:]), meta=metadata)
            return result, can_submit, p.revision
        return Result[Check](data=check, meta=metadata)

    return run(auth, op, read)


def listing(auth, pid):
    def read(c, a, _):
        p = lock_project(c, a, pid)
        scope = [reviews.c.project_id == pid]
        if not p.materials_visible:
            scope.append(reviews.c.type == 'charter')
        total = c.scalar(sa.select(sa.func.count()).select_from(reviews).where(*scope))
        rows = c.execute(sa.select(reviews).where(*scope)
                         .order_by(reviews.c.created_at.desc(), reviews.c.id).limit(PAGE_SIZE)).mappings()
        items = [view(row) for row in rows]
        metadata = meta(LIST, [f'review:{i.reviewId}@{i.revision}' for i in items])
        return Result[ReviewPage](data=ReviewPage(items=items, page=1, pageSize=PAGE_SIZE, total=total, asOf=metadata.asOf), meta=metadata)

    return run(auth, LIST, read)


def transition_project(c, a, pid, gate: Gate, expected, key, rid):
    """Execute the authorized submission edge, never a decision or a direct stage patch."""
    if SUBMIT not in a.actions:
        raise Failure(403, 'FORBIDDEN')
    p = lock_project(c, a, pid, write=True)
    if not submitter(c, a, p, gate):
        raise Failure(403, 'FORBIDDEN', project_id=pid)
    signature = fingerprint([str(pid), gate, expected])
    try:
        prior = replay(c, a, SUBMIT, key, signature, Result[ReviewView])
    except Failure as e:
        e.project_id = pid
        raise
    if prior:
        return prior
    if p.revision != expected:
        raise Failure(409, 'VERSION_CONFLICT', project_id=pid)
    if p.stage != ENTRY[gate]:
        raise Failure(422, 'GATE_STAGE_MISMATCH', project_id=pid)
    if c.scalar(sa.select(sa.exists(sa.select(reviews.c.id).where(reviews.c.project_id == pid, reviews.c.status == 'pending')))):
        raise Failure(409, 'REVIEW_ALREADY_PENDING', project_id=pid)

    check, materials = evaluate(c, a, p, gate)
    if not check.eligible:
        raise Failure(422, 'GATE_REQUIREMENTS_NOT_MET', project_id=pid)
    round_no = c.scalar(sa.select(sa.func.coalesce(sa.func.max(reviews.c.round), 0) + 1)
                        .where(reviews.c.project_id == pid, reviews.c.type == gate))
    row = c.execute(reviews.insert().values(project_id=pid, created_by=a.person, round=round_no, type=gate,
                    status='pending', attendees=[], snapshot_hash=check.snapshotHash).returning(reviews)).mappings().one()
    review_id = row.id
    c.execute(snapshots.insert().values(review_id=review_id, rule_version=RULE_VERSION, snapshot_hash=check.snapshotHash,
                                       materials=materials.content, source_refs=refs(p, materials)))
    dest = REVIEW[gate]
    c.execute(db.projects.update().where(db.projects.c.id == pid).values(stage=dest, revision=p.revision + 1, updated_at=sa.func.now()))
    c.execute(transitions.insert().values(project_id=pid, from_revision=p.revision, to_revision=p.revision + 1,
        from_stage=p.stage, to_stage=dest, review_id=review_id, actor_id=a.person, actor_type=a.kind, request_id=UUID(rid)))
    result = Result[ReviewView](data=view(row), meta=meta(SUBMIT, [f'review:{review_id}@{row.revision}',
        f'project:{pid}@{p.revision + 1}', f'review-snapshot:{review_id}']))
    body = result.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, SUBMIT, rid, review_id, pid, signature, body, fingerprint(body),
                   fingerprint({'stage': p.stage, 'revision': p.revision}))
    c.execute(db.outbox_events.insert().values(dedupe_key=rid, event_type='review.submitted', status='pending',
        payload={'projectId': str(pid), 'reviewId': str(review_id), 'stage': dest, 'revision': p.revision + 1,
                 'snapshotHash': check.snapshotHash}))
    return result


def submit(auth, pid, gate: Gate, expected, key):
    return run(auth, SUBMIT, lambda c, a, rid: transition_project(c, a, pid, gate, expected, key, rid))
