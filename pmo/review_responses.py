"""Written PM responses attached to charter strengthening and checked at feasibility."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from pydantic import AfterValidator, Field, StringConstraints
from sqlalchemy.dialects.postgresql import JSONB

from .contracts import Strict
from .evidence import lookup, lookup_for_integrity, require_verified_content
from .identity import Failure

bindings = sa.table('review_strengthening_tasks', sa.column('review_id', sa.Uuid),
    sa.column('task_id', sa.Uuid), sa.column('item_key'), sa.column('evidence_required', sa.Boolean))
responses = sa.table('review_reinforcement_responses', sa.column('id', sa.Uuid), sa.column('task_id', sa.Uuid),
    sa.column('revision', sa.BigInteger), sa.column('submitted_by', sa.Uuid),
    sa.column('submitted_at', sa.DateTime(timezone=True)), sa.column('response_text'), sa.column('evidence', JSONB))


def plain(value: str) -> str:
    if '\x00' in value or not any(char.isalnum() for char in value):
        raise ValueError('Explicit text is required')
    value.encode('utf-8')
    return value


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=6000), AfterValidator(plain)]
EvidenceRef = Annotated[str, StringConstraints(pattern=r'^evidence:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]


class Command(Strict):
    responseText: Text
    evidenceRefs: list[EvidenceRef] = Field(default_factory=list, max_length=12, json_schema_extra={'uniqueItems': True})


class Response(Strict):
    responseId: UUID
    taskId: UUID
    reviewId: UUID
    itemKey: str
    revision: int
    responseText: str
    evidenceRefs: list[EvidenceRef]
    submittedBy: UUID
    submittedAt: datetime


def binding(c, task_id):
    return c.execute(sa.select(bindings).where(bindings.c.task_id == task_id)).mappings().one_or_none()


def latest(c, task_id):
    return c.execute(sa.select(responses).where(responses.c.task_id == task_id)
                     .order_by(responses.c.revision.desc()).limit(1)).mappings().one_or_none()


def view(row, review_id, item_key) -> Response:
    return Response(responseId=row.id, taskId=row.task_id, reviewId=review_id, itemKey=item_key,
        revision=row.revision, responseText=row.response_text, evidenceRefs=[item['ref'] for item in row.evidence],
        submittedBy=row.submitted_by, submittedAt=row.submitted_at)


def source_ref(row):
    return f'review-response:{row.id}@{row.revision}'


def checked_evidence(c, a, p, refs, *, persisted=False):
    if len(set(refs)) != len(refs):
        raise Failure(422, 'REVIEW_RESPONSE_EVIDENCE_INVALID', project_id=p.id)
    proofs = []
    for ref in sorted(refs):
        try:
            eid = UUID(ref.removeprefix('evidence:'))
            row = lookup_for_integrity(c, p.id, eid) if persisted else lookup(c, a, eid, True, shared=True)
            if row.project_id != p.id:
                raise Failure(422, 'EVIDENCE_TARGET_MISMATCH')
            require_verified_content(row)
        except (ValueError, Failure):
            raise Failure(422, 'REVIEW_RESPONSE_EVIDENCE_INVALID', project_id=p.id) from None
        proofs.append({'ref': ref, 'revision': row.revision, 'sha256': row.sha256, 'verifiedBy': str(row.verified_by)})
    return proofs
