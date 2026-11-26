"""Synthetic DEC-09 written acceptance. Only the gate service can decide closure."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Annotated, Any, Literal
from unicodedata import category, normalize
from uuid import UUID, uuid4

from fastapi import APIRouter
from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints, ValidationError, model_validator
from pypdf import PdfReader
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from . import evidence_storage, schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import RULE_VERSION, Result, Strict
from .evidence import links, stmt as evidence_stmt
from .identity import Failure, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_assignment
from .policy import guard, receipt_matches, require, selection, status
from .scope_quality import metrics

GET = 'human_requirement_exception_get'
SIGN = 'human_requirement_exception_sign'
REVOKE = 'human_requirement_exception_revoke'
PATH = '/projects/{projectId}/requirement-exceptions'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
router = APIRouter(prefix='/api/v1', tags=['Written requirement acceptance'])
POLICY_VERSION = 'synthetic-dec09-v1'
ACTIVE_STAGES = ('delivering', 'closing_review')
MAX_ITEMS = 30
MAX_PDF_BYTES = 1024 * 1024
MAX_TTL = timedelta(days=30)
BIGINT_MAX = 2**63 - 1

events = sa.table('requirement_exception_events',
    *(sa.column(k, sa.Uuid) for k in ('id', 'project_id', 'actor_id', 'assignment_id', 'evidence_id', 'revokes_id', 'request_id')),
    sa.column('revision', sa.BigInteger), sa.column('kind'), sa.column('created_at', sa.DateTime(timezone=True)),
    sa.column('payload', JSONB), sa.column('fingerprint'))
transitions = sa.table('requirement_transitions', sa.column('requirement_id', sa.Uuid),
    sa.column('baseline_version', sa.Integer), sa.column('actor_id', sa.Uuid))


def _plain(text: str) -> str:
    text = normalize('NFC', ' '.join(text.split()))
    if not any(ch.isalnum() for ch in text) or any(category(ch).startswith('C') for ch in text):
        raise ValueError('Expected a substantive statement without control characters')
    return text


def _utc(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError('Expiry outside supported UTC range') from exc


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec='microseconds').replace('+00:00', 'Z')


Text = Annotated[str, AfterValidator(_plain), StringConstraints(min_length=5, max_length=1000)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
EvidenceRef = Annotated[str, Field(pattern=r'^evidence:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$')]
UtcTime = Annotated[AwareDatetime, AfterValidator(_utc)]
ReqRevision = Annotated[int, Field(strict=True, ge=1, le=BIGINT_MAX)]
BaselineVersion = Annotated[int, Field(strict=True, ge=1, le=2**31 - 1)]


class RequirementVersion(Strict):
    requirementId: UUID
    revision: ReqRevision
    baselineVersion: BaselineVersion
    requirementHash: Hash


class Gap(RequirementVersion):
    priority: Literal['P0', 'P1', 'P2']
    desc: str
    acceptanceCriteria: str
    status: Literal['baseline', 'delivered', 'accepted']


class AcceptedItem(RequirementVersion):
    deviation: Text


class AcceptanceScope(Strict):
    decision: Literal['accept']
    gapHash: Hash
    items: list[AcceptedItem] = Field(min_length=1, max_length=MAX_ITEMS)
    expiresAt: UtcTime

    @model_validator(mode='after')
    def unique_items(self):
        if len({item.requirementId for item in self.items}) != len(self.items):
            raise ValueError('Each requirement must occur exactly once')
        return self


class Sign(AcceptanceScope):
    evidenceRef: EvidenceRef


class Revoke(Strict):
    reason: Text


class Proof(Strict):
    evidenceRef: EvidenceRef
    evidenceSha256: Hash
    evidenceRevision: Annotated[int, Field(strict=True, ge=2, le=BIGINT_MAX)]
    linkedRequirementId: UUID
    verifiedBy: UUID
    verifiedAt: UtcTime


class SignedPayload(AcceptanceScope):
    ruleVersion: str
    policyVersion: Literal['synthetic-dec09-v1']
    customerStakeholderId: UUID
    verifierAssignmentId: UUID
    evidenceUploadedBy: UUID
    evidenceOwnerId: UUID
    proof: Proof


class Receipt(AcceptanceScope, Proof):
    exceptionId: UUID
    projectId: UUID
    revision: int
    signedBy: UUID
    signedAt: datetime
    receiptHash: Hash
    policyStatus: Literal['POLICY_PENDING', 'POLICY_ACTIVE'] = 'POLICY_PENDING'


class Revocation(Strict):
    exceptionId: UUID
    revocationId: UUID
    projectId: UUID
    revision: int
    revokedBy: UUID
    revokedAt: datetime
    receiptHash: Hash
    policyStatus: Literal['POLICY_PENDING', 'POLICY_ACTIVE'] = 'POLICY_PENDING'


class State(Strict):
    projectId: UUID
    revision: int
    gapHash: Hash | None = None
    gaps: list[Gap] = Field(max_length=MAX_ITEMS)
    gapsTruncated: bool = False
    current: Receipt | None = None
    canSign: bool = False
    canRevoke: bool = False
    signerPersonId: UUID | None = None
    revocableExceptionId: UUID | None = None
    policyStatus: Literal['POLICY_PENDING', 'POLICY_ACTIVE'] = 'POLICY_PENDING'


@dataclass(frozen=True)
class GateException:
    snapshot: dict[str, Any]
    source_refs: list[str]


@dataclass(frozen=True)
class _Basis:
    digest: str
    gaps: list[Gap]
    latest_gap_update: datetime
    truncated: bool


def _scope_items(command: AcceptanceScope) -> list[AcceptedItem]:
    return sorted(command.items, key=lambda item: item.requirementId)


def written_acceptance(project_id: UUID, signer_id: UUID, command: AcceptanceScope) -> str:
    """Canonical text for a text-bearing PDF; authenticated Sign is the signature."""
    lines = [
        'PMO DEC-09 SYNTHETIC REQUIREMENT EXCEPTION',
        'I explicitly accept only the listed requirement deviations for conditional closure.',
        f'Project: {project_id}',
        f'Customer: {signer_id}',
        f'Gap SHA-256: {command.gapHash}',
        f'Expires at (UTC): {_stamp(command.expiresAt)}',
    ]
    for item in _scope_items(command):
        lines.extend([f'Requirement: {item.requirementId} revision {item.revision} '
                      f'baseline {item.baselineVersion} hash {item.requirementHash}', f'Deviation: {item.deviation}'])
    return '\n'.join(lines)


def _matches_pdf(original: bytes, text: str) -> bool:
    if not 0 < len(original) <= MAX_PDF_BYTES:
        return False
    try:
        pdf = PdfReader(BytesIO(original), strict=True)
        if pdf.is_encrypted or not 1 <= len(pdf.pages) <= 20:
            return False
        actual = ' '.join(' '.join(page.extract_text().split()) for page in pdf.pages)
        return normalize('NFC', ' '.join(actual.split())) == ' '.join(text.split())
    except Exception:
        return False


def _requirement_hash(row) -> str:
    return fingerprint(['dec09-requirement-v1', str(row.project_id), str(row.id), row.revision, row.baseline_version,
        row['desc'], row.acceptance_criteria, row.priority, row.status, row.planned_confirm_at.isoformat(),
        row.evidence_ref, str(row.accepted_by) if row.accepted_by else None,
        _stamp(row.accepted_at) if row.accepted_at else None])


def _basis(c, p) -> _Basis | None:
    if p.stage not in ACTIVE_STAGES:
        return None
    q, _ = metrics(c, p.id)
    if q.gaps or q.frozenCount or q.activeCount == 0 or not q.activeWeight:
        return None
    if q.activeP0 == q.confirmedP0 and 10 * q.confirmedWeight >= 9 * q.activeWeight:
        return None
    from .gates import signed_baseline
    if not signed_baseline(c, p.id):
        return None
    r = db.requirements
    rows = c.execute(sa.select(r).where(r.c.project_id == p.id, r.c.status != 'cancelled')
                     .order_by(r.c.id).with_for_update(read=True)).mappings().all()
    if len(rows) != q.activeCount or any(not row.baseline_version or not row.planned_confirm_at for row in rows):
        return None
    hashes = [_requirement_hash(row) for row in rows]
    missing = [(row, digest) for row, digest in zip(rows, hashes) if row.status != 'customer_confirmed']
    if not missing:
        return None
    gaps = [Gap(requirementId=row.id, revision=row.revision, baselineVersion=row.baseline_version, requirementHash=digest,
                desc=row['desc'], acceptanceCriteria=row.acceptance_criteria, priority=row.priority, status=row.status)
            for row, digest in missing[:MAX_ITEMS]]
    return _Basis(fingerprint([POLICY_VERSION, RULE_VERSION, str(p.id), hashes]), gaps,
                  max(row.updated_at for row, _ in missing), len(missing) > MAX_ITEMS)


def _covered(basis: _Basis, command: AcceptanceScope) -> bool:
    return (not basis.truncated and command.gapHash == basis.digest and len(command.items) == len(basis.gaps)
            and all(item.model_dump(exclude={'deviation'}) == gap.model_dump(include=set(RequirementVersion.model_fields))
                    for item, gap in zip(_scope_items(command), basis.gaps)))


def _client(c, p, person, *, stakeholder_id=None):
    if person in (p.initiator_id, p.pm_id, p.sponsor_id) or active_assignment(c, p, person, 'reviewer') is not None:
        return None
    assignment = active_assignment(c, p, person, 'client_representative')
    if assignment is None:
        return None
    t = db.stakeholders.c
    stmt = sa.select(t.id).where(t.project_id == p.id, t.type == 'customer', t.linked_person_id == person)
    if stakeholder_id is not None:
        stmt = stmt.where(t.id == stakeholder_id)
    contact = c.scalar(stmt.order_by(t.id).limit(1).with_for_update(read=True))
    return (assignment, contact) if contact is not None else None


def _independent(c, p, person, basis: _Basis):
    r, t = db.requirements.c, transitions.c
    return not c.scalar(sa.select(sa.exists(sa.select(1).select_from(db.requirements.outerjoin(
        transitions, sa.and_(t.requirement_id == r.id, t.baseline_version == r.baseline_version)))
        .where(r.project_id == p.id, r.id.in_([gap.requirementId for gap in basis.gaps]),
               sa.or_(r.created_by == person, r.accepted_by == person, t.actor_id == person)))))


def _proof(c, p, person, basis: _Basis, command: AcceptanceScope, evidence_id, now):
    e = db.evidence_files
    row = c.execute(evidence_stmt().where(e.c.id == evidence_id, e.c.project_id == p.id)
        .with_for_update(read=True, of=(e, links))).mappings().one_or_none()
    if row is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND', project_id=p.id)
    if row.resource_type != 'requirement' or row.resource_id not in {gap.requirementId for gap in basis.gaps}:
        raise Failure(422, 'EVIDENCE_TARGET_MISMATCH', project_id=p.id)
    if (row.mime_type != 'application/pdf' or not 0 < row.size_bytes <= MAX_PDF_BYTES
            or row.quarantine_status != 'verified' or row.revision < 2 or row.verified_at is None
            or row.verified_by in (None, person, row.owner_id, row.uploaded_by, p.initiator_id, p.pm_id, p.sponsor_id)
            or not basis.latest_gap_update <= row.created_at <= row.verified_at <= now):
        raise Failure(422, 'REQUIREMENT_EXCEPTION_EVIDENCE_REQUIRED', project_id=p.id)
    assignment = active_assignment(c, p, row.verified_by, 'reviewer')
    if not assignment or assignment.accepted_at > row.verified_at:
        raise Failure(422, 'REQUIREMENT_EXCEPTION_EVIDENCE_REQUIRED', project_id=p.id)
    try:
        original = evidence_storage.checked_bytes(row.storage_key, row.sha256, row.size_bytes)
    except Failure as exc:
        exc.project_id = p.id
        raise
    if not _matches_pdf(original, written_acceptance(p.id, person, command)):
        raise Failure(422, 'REQUIREMENT_EXCEPTION_SCOPE_NOT_IN_ORIGINAL', project_id=p.id)
    return (Proof(evidenceRef=f'evidence:{row.id}', evidenceSha256=row.sha256, evidenceRevision=row.revision,
                  linkedRequirementId=row.resource_id, verifiedBy=row.verified_by, verifiedAt=row.verified_at),
            assignment.id, row.uploaded_by, row.owner_id)


def _event_hash(row) -> str:
    return fingerprint(['dec09-event-v1', {k: _stamp(v) if isinstance(v, datetime) else str(v) if isinstance(v, UUID) else v
                                           for k, v in row.items() if k != 'fingerprint'}])


def _latest(c, pid):
    return c.execute(sa.select(events).where(events.c.project_id == pid).order_by(events.c.revision.desc())
                     .limit(1)).mappings().one_or_none()


def _current(c, p, basis, row, now) -> SignedPayload | None:
    if basis is None or row is None or row.kind != 'signed' or row.project_id != p.id:
        return None
    if not receipt_matches(c, p, 'requirement.exception', row.request_id):
        return None
    try:
        signed = SignedPayload.model_validate(row.payload)
        if (signed.ruleVersion != RULE_VERSION or not row.created_at <= now < signed.expiresAt
                or signed.expiresAt - row.created_at > MAX_TTL or not _covered(basis, signed)
                or row.revokes_id is not None or row.evidence_id is None or row.fingerprint != _event_hash(row)):
            return None
        authority = _client(c, p, row.actor_id, stakeholder_id=signed.customerStakeholderId)
        if (authority is None or authority[0].id != row.assignment_id or authority[0].accepted_at > row.created_at
                or not _independent(c, p, row.actor_id, basis)):
            return None
        proof, assignment, uploader, owner = _proof(c, p, row.actor_id, basis, signed, row.evidence_id, now)
        if (proof != signed.proof or proof.verifiedAt > row.created_at or assignment != signed.verifierAssignmentId
                or uploader != signed.evidenceUploadedBy or owner != signed.evidenceOwnerId
                or _now(c) >= signed.expiresAt):
            return None
        return signed
    except (Failure, ValidationError):
        return None


def _view(c, p, row, signed: SignedPayload) -> Receipt:
    return Receipt(**signed.model_dump(include=set(AcceptanceScope.model_fields)), **signed.proof.model_dump(),
        exceptionId=row.id, projectId=row.project_id, revision=row.revision, signedBy=row.actor_id,
        signedAt=row.created_at, receiptHash=row.fingerprint, policyStatus=status(c, p, 'requirement.exception'))


def _refs(receipt: Receipt) -> list[str]:
    return [f'requirement-exception:{receipt.exceptionId}@{receipt.revision}', receipt.evidenceRef,
            *(f'requirement:{item.requirementId}@{item.revision}' for item in receipt.items)]


def _project(c, a, pid, *, write):
    if a.kind != 'human':
        raise Failure(403, 'FORBIDDEN')
    p = db.projects
    row = c.execute(sa.select(p).where(p.c.id == pid, visible(a)).with_for_update(read=not write)).mappings().one_or_none()
    if row is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return row


def _now(c):
    return c.scalar(sa.select(sa.func.clock_timestamp()))


def gate_check(c, a, p) -> GateException | None:
    """Caller holds the project lock. Recompute the entire requirements-only exception."""
    if p.stage not in ACTIVE_STAGES or a.org != p.org_id:
        return None
    if not c.scalar(sa.select(db.projects.c.id).where(db.projects.c.id == p.id, visible(a))):
        return None
    row = _latest(c, p.id)
    if row is None or row.kind != 'signed':
        return None
    signed = _current(c, p, _basis(c, p), row, _now(c))
    if signed is None:
        return None
    receipt = _view(c, p, row, signed)
    return GateException(receipt.model_dump(mode='json'), _refs(receipt))


@router.get(PATH, operation_id=GET, response_model_exclude_none=True, openapi_extra=HUMAN)
def get(projectId: UUID, authorization: Auth) -> Result[State]:
    def read(c, a, _):
        p = _project(c, a, projectId, write=False)
        basis = _basis(c, p)
        enabled = selection(c, p, 'requirement.exception') is not None
        row = _latest(c, p.id)
        signed = _current(c, p, basis, row, _now(c)) if row is not None else None
        revision = row.revision if row is not None else 1
        current = _view(c, p, row, signed) if signed is not None else None
        authority = _client(c, p, a.person) if enabled and p.stage in ACTIVE_STAGES else None
        can_sign = (SIGN in a.actions and p.stage == 'delivering' and authority is not None and current is None and revision < BIGINT_MAX
                    and basis is not None and not basis.truncated and _independent(c, p, a.person, basis))
        can_revoke = (REVOKE in a.actions and authority is not None and row is not None and row.kind == 'signed' and row.actor_id == a.person
                      and revision < BIGINT_MAX)
        state = State(projectId=p.id, revision=revision, gapHash=basis.digest if basis else None,
            gaps=basis.gaps if basis else [], gapsTruncated=basis.truncated if basis else False, current=current,
            canSign=can_sign, canRevoke=can_revoke, signerPersonId=a.person if authority is not None else None,
            revocableExceptionId=row.id if can_revoke else None, policyStatus=status(c, p, 'requirement.exception'))
        return Result[State](data=state, meta=meta(GET, _refs(current) if current else [f'project:{p.id}@{p.revision}']))

    return run(authorization, GET, read)


def _write_context(c, a, pid):
    p = _project(c, a, pid, write=True)
    authority = _client(c, p, a.person)
    if authority is None:
        raise Failure(403, 'INDEPENDENT_CUSTOMER_REQUIRED', project_id=pid)
    guard(c, p, 'requirement.exception')
    return p, authority


def _next_revision(p, last, expected):
    revision = last.revision if last is not None else 1
    if revision != int(expected) or revision == BIGINT_MAX:
        raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
    return revision + 1


def _append(c, p, a, rid, revision, assignment, kind, payload, *, now, evidence=None, revokes=None):
    values = dict(id=uuid4(), project_id=p.id, revision=revision, kind=kind, actor_id=a.person, assignment_id=assignment,
        evidence_id=evidence, revokes_id=revokes, request_id=UUID(rid), created_at=now, payload=payload)
    return c.execute(events.insert().values(**values, fingerprint=_event_hash(values)).returning(events)).mappings().one()


def _finish(c, p, a, rid, key, op, signature, row, result, before):
    response = result.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, row.id, p.id, signature, response, fingerprint(response), before)
    c.execute(db.outbox_events.insert().values(dedupe_key=rid, event_type=f'requirement_exception.{row.kind}', status='pending',
        payload={'projectId': str(p.id), 'exceptionId': str(row.revokes_id or row.id), 'revision': row.revision}))
    return result


@router.post(PATH, operation_id=SIGN, openapi_extra=HUMAN)
def sign(projectId: UUID, command: Sign, authorization: Auth, key: Key, expected: Revision) -> Result[Receipt]:
    normalized = command.model_dump(mode='json', exclude={'items'})
    normalized['items'] = [item.model_dump(mode='json') for item in _scope_items(command)]
    signature = fingerprint([str(projectId), int(expected), normalized])

    def write(c, a, rid):
        p, (assignment, contact) = _write_context(c, a, projectId)
        prior = replay(c, a, SIGN, key, signature, Result[Receipt])
        if prior is not None:
            return prior
        require(c, p, 'requirement.exception', rid)
        if p.stage != 'delivering':
            raise Failure(422, 'REQUIREMENT_EXCEPTION_LOCKED', project_id=p.id)
        last = _latest(c, p.id)
        revision = _next_revision(p, last, expected)
        basis = _basis(c, p)
        if basis is None:
            raise Failure(422, 'REQUIREMENT_EXCEPTION_GAPS_REQUIRED', project_id=p.id)
        if command.gapHash != basis.digest:
            raise Failure(409, 'REQUIREMENT_EXCEPTION_SCOPE_STALE', project_id=p.id)
        if not _covered(basis, command):
            raise Failure(422, 'REQUIREMENT_EXCEPTION_SCOPE_INCOMPLETE', project_id=p.id)
        if not _independent(c, p, a.person, basis):
            raise Failure(403, 'INDEPENDENT_CUSTOMER_REQUIRED', project_id=p.id)
        now = _now(c)
        if not now < command.expiresAt <= now + MAX_TTL:
            raise Failure(422, 'INVALID_REQUIREMENT_EXCEPTION_EXPIRY', project_id=p.id)
        if _current(c, p, basis, last, now) is not None:
            raise Failure(409, 'REQUIREMENT_EXCEPTION_ALREADY_SIGNED', project_id=p.id)
        evidence_id = UUID(command.evidenceRef.removeprefix('evidence:'))
        proof, verifier, uploader, owner = _proof(c, p, a.person, basis, command, evidence_id, now)
        now = _now(c)
        if now >= command.expiresAt:
            raise Failure(422, 'INVALID_REQUIREMENT_EXCEPTION_EXPIRY', project_id=p.id)
        signed = SignedPayload(decision='accept', gapHash=command.gapHash, items=_scope_items(command), expiresAt=command.expiresAt,
            ruleVersion=RULE_VERSION, policyVersion=POLICY_VERSION, customerStakeholderId=contact, proof=proof,
            verifierAssignmentId=verifier, evidenceUploadedBy=uploader, evidenceOwnerId=owner)
        row = _append(c, p, a, rid, revision, assignment.id, 'signed', signed.model_dump(mode='json'), now=now, evidence=evidence_id)
        receipt = _view(c, p, row, signed)
        result = Result[Receipt](data=receipt, meta=meta(SIGN, _refs(receipt)))
        return _finish(c, p, a, rid, key, SIGN, signature, row, result, last.fingerprint if last is not None else None)

    return run(authorization, SIGN, write)


@router.post(PATH + '/{exceptionId}/revoke', operation_id=REVOKE, openapi_extra=HUMAN)
def revoke(projectId: UUID, exceptionId: UUID, command: Revoke, authorization: Auth, key: Key,
           expected: Revision) -> Result[Revocation]:
    signature = fingerprint([str(projectId), str(exceptionId), int(expected), command.model_dump(mode='json')])

    def write(c, a, rid):
        p, (assignment, _) = _write_context(c, a, projectId)
        prior = replay(c, a, REVOKE, key, signature, Result[Revocation])
        if prior is not None:
            return prior
        require(c, p, 'requirement.exception', rid)
        if p.stage not in ACTIVE_STAGES:
            raise Failure(422, 'REQUIREMENT_EXCEPTION_LOCKED', project_id=p.id)
        last = _latest(c, p.id)
        revision = _next_revision(p, last, expected)
        if last is None or last.id != exceptionId or last.kind != 'signed':
            raise Failure(404, 'RESOURCE_NOT_FOUND', project_id=p.id)
        if last.actor_id != a.person:
            raise Failure(403, 'REQUIREMENT_EXCEPTION_SIGNER_REQUIRED', project_id=p.id)
        payload = {'reason': command.reason, 'revokedReceiptHash': last.fingerprint,
                   'ruleVersion': RULE_VERSION, 'policyVersion': POLICY_VERSION}
        row = _append(c, p, a, rid, revision, assignment.id, 'revoked', payload, now=_now(c), revokes=last.id)
        result = Result[Revocation](data=Revocation(exceptionId=last.id, revocationId=row.id, projectId=p.id,
            revision=row.revision, revokedBy=a.person, revokedAt=row.created_at, receiptHash=row.fingerprint,
            policyStatus=status(c, p, 'requirement.exception')),
            meta=meta(REVOKE, [f'requirement-exception:{last.id}@{last.revision}',
                              f'requirement-exception-revocation:{row.id}@{row.revision}']))
        return _finish(c, p, a, rid, key, REVOKE, signature, row, result, last.fingerprint)

    return run(authorization, REVOKE, write)
