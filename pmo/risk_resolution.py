"""Human risk decisions backed by private evidence and append-only receipts."""
from datetime import UTC, datetime
from io import BytesIO
import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints, ValidationError
from pypdf import PdfReader
from sqlalchemy.dialects.postgresql import JSONB

from . import evidence_storage as storage, schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .evidence import links as evidence_links, stmt as evidence_query
from .identity import Failure, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_assignment
from .risks import WRITABLE, Risk, view

CLOSE = 'human_risk_close'
HANDOFF = 'human_risk_handoff'
ACCEPTANCE = 'human_risk_handoff_acceptance'
GET = 'human_risk_resolution_get'
PATH = '/risks/{riskId}'
ACCEPTANCE_LIMIT = 20
router = APIRouter(prefix='/api/v1', tags=['Risks'])
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
HUMAN_REVIEWER = {**HUMAN, 'x-requiredRole': 'authorized_reviewer'}


def _receipt_columns():
    return (
        *(sa.column(name, sa.Uuid) for name in ('id', 'project_id', 'risk_id', 'evidence_id',
          'actor_id', 'reviewer_assignment_id', 'receiver_id', 'request_id')),
        sa.column('evidence_sha256'), sa.column('evidence_revision', sa.BigInteger),
        sa.column('kind'), sa.column('fingerprint'), sa.column('created_at', sa.DateTime(timezone=True)),
    )


acceptances = sa.table('risk_handoff_acceptances', *_receipt_columns(),
    sa.column('risk_revision', sa.BigInteger), sa.column('risk_fingerprint'), sa.column('scope'),
    sa.column('request_receipt_id', sa.Uuid), sa.column('verification', JSONB(none_as_null=True)))
resolutions = sa.table('risk_resolutions', *_receipt_columns(),
    sa.column('prior_revision', sa.BigInteger), sa.column('revision', sa.BigInteger), sa.column('acceptance_id', sa.Uuid))

EvidenceRef = Annotated[str, Field(pattern=r'^evidence:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')]
AcceptanceKind = Literal['requested', 'receiver_confirmed', 'reviewer_verified']


def _plain_text(text: str) -> str:
    if '\x00' in text:
        raise ValueError('Expected text without NUL characters')
    text.encode('utf-8')
    return text


Scope = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=3000), AfterValidator(_plain_text)]
IdentityBasis = Annotated[str, StringConstraints(strip_whitespace=True, min_length=12, max_length=1000), AfterValidator(_plain_text)]
Excerpt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=12, max_length=4000), AfterValidator(_plain_text)]


class Close(Strict):
    resolutionEvidenceRef: EvidenceRef


class Handoff(Strict):
    receiverPersonId: UUID
    acceptanceEvidenceRef: EvidenceRef


class AcceptanceRequest(Handoff):
    acceptanceScope: Scope
    kind: Literal['request']


class AcceptanceConfirm(Handoff):
    acceptanceScope: Scope
    kind: Literal['confirm']
    decision: Literal['accept']


class ExternalVerification(Strict):
    decision: Literal['accept']
    acceptedAt: AwareDatetime
    identityVerification: IdentityBasis
    acceptanceExcerpt: Excerpt = Field(description=(
        'Verbatim PDF clause: I explicitly accept risk {riskId} revision {riskRevision} as receiver '
        '{receiverPersonId} for scope: {acceptanceScope}'))


class AcceptanceVerify(Handoff, ExternalVerification):
    acceptanceScope: Scope
    kind: Literal['verify']


AcceptanceCommand = Annotated[AcceptanceRequest | AcceptanceConfirm | AcceptanceVerify, Field(discriminator='kind')]


class _AcceptanceEvidence(Strict):
    acceptanceId: UUID
    riskId: UUID
    riskRevision: int
    receiverPersonId: UUID
    acceptanceScope: str
    acceptanceEvidenceRef: str
    evidenceSha256: str
    evidenceRevision: int
    createdAt: datetime


class AcceptanceSummary(_AcceptanceEvidence):
    kind: AcceptanceKind


class Acceptance(AcceptanceSummary):
    projectId: UUID
    actorPersonId: UUID


class Resolution(Risk):
    resolutionId: UUID
    reviewerPersonId: UUID
    receiverPersonId: UUID | None = None
    acceptanceId: UUID | None = None
    evidenceRef: str
    evidenceSha256: str
    evidenceRevision: int
    closedAt: datetime | None = None


class AcceptanceState(AcceptanceSummary):
    status: Literal['pending', 'accepted']
    canConfirm: bool
    canHandoff: bool
    projectId: UUID
    actorPersonId: UUID


class Nomination(_AcceptanceEvidence):
    kind: Literal['requested'] = 'requested'
    status: Literal['pending'] = 'pending'
    canConfirm: Literal[True] = True
    canHandoff: Literal[False] = False


class RiskRevision(Strict):
    riskId: UUID
    revision: int


class ProjectResolutionState(Strict):
    audience: Literal['project'] = 'project'
    risk: Risk
    resolution: Resolution | None = None
    status: Literal['unresolved', 'pending', 'accepted', 'resolved']
    acceptances: list[AcceptanceState] = Field(max_length=ACCEPTANCE_LIMIT)
    acceptancesTruncated: bool = False
    canClose: bool = False
    canRequestAcceptance: bool = False
    canVerifyAcceptance: bool = False


class ReceiverResolutionState(Strict):
    audience: Literal['receiver'] = 'receiver'
    risk: RiskRevision
    status: Literal['pending'] = 'pending'
    acceptances: list[Nomination] = Field(min_length=1, max_length=ACCEPTANCE_LIMIT)
    acceptancesTruncated: bool = False
    canClose: Literal[False] = False
    canRequestAcceptance: Literal[False] = False
    canVerifyAcceptance: Literal[False] = False


ResolutionState = Annotated[ProjectResolutionState | ReceiverResolutionState, Field(discriminator='audience')]


def _json_fields(row):
    return {key: (value.astimezone(UTC).isoformat() if isinstance(value, datetime) else
        str(value) if isinstance(value, UUID) else value) for key, value in row.items()}


def _receipt_hash(domain, row, *bindings):
    return fingerprint([domain, _json_fields({k: v for k, v in row.items() if k != 'fingerprint'}), *bindings])


def _risk_subject(risk, revision):
    # Finalization changes state, timestamps and revision, not the accepted subject.
    fields = ('id', 'project_id', 'created_by', 'owner_id', 'statement', 'prob', 'impact', 'response',
              'is_blocking', 'blocked_since', 'created_at')
    return fingerprint(['risk-subject-v1', revision, _json_fields({k: risk[k] for k in fields})])


def _proof_binding(proof):
    fields = ('id', 'project_id', 'resource_type', 'resource_id', 'sha256', 'size_bytes',
              'mime_type', 'revision', 'uploaded_by', 'owner_id', 'verified_by', 'verified_at')
    return _json_fields({k: proof[k] for k in fields})


def _proof(c, p, risk, eid):
    e = db.evidence_files
    # Project or nominated receiver authority is checked before this private lookup.
    proof = c.execute(evidence_query().where(e.c.id == eid, e.c.project_id == p.id)
        .with_for_update(read=True, of=(e, evidence_links))).mappings().one_or_none()
    if proof is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND', project_id=p.id)
    if proof.resource_type != 'risk' or proof.resource_id != risk.id:
        raise Failure(422, 'EVIDENCE_TARGET_MISMATCH', project_id=p.id)
    if (proof.quarantine_status != 'verified' or proof.verified_at is None or proof.verified_by is None
            or proof.verified_by in (proof.uploaded_by, proof.owner_id) or proof.revision < 2):
        raise Failure(422, 'VERIFIED_EVIDENCE_REQUIRED', project_id=p.id)
    return proof, storage.checked_bytes(proof.storage_key, proof.sha256, proof.size_bytes)


def _reviewer(c, p, risk, person, receiver=None, proof=None):
    if person in (p.initiator_id, p.pm_id, p.sponsor_id, risk.owner_id, risk.created_by, receiver):
        return None
    if proof is not None and person in (proof.uploaded_by, proof.owner_id):
        return None
    return active_assignment(c, p, person, 'reviewer')


def _receiver(c, p, risk, person):
    if person in (None, risk.owner_id, risk.created_by):
        return False
    n = db.persons
    return c.scalar(sa.select(n.c.id).where(n.c.id == person, n.c.org_id == p.org_id, n.c.is_active)
        .with_for_update(read=True)) is not None


def _project_and_risk(c, a, risk_id, *, confirming=None):
    if a.kind != 'human':
        raise Failure(403, 'FORBIDDEN')
    p, r = db.projects, db.risks
    authority = visible(a)
    if confirming is not None:
        # Nomination authorizes only this confirmation, not access to the project or its files.
        t = acceptances.c
        authority = sa.and_(p.c.org_id == a.org, sa.or_(authority, sa.exists(sa.select(1).where(
            t.project_id == p.c.id, t.risk_id == risk_id, t.receiver_id == a.person,
            t.evidence_id == confirming, t.kind == 'requested'))))
    project = c.execute(sa.select(p).join(r, r.c.project_id == p.c.id).where(r.c.id == risk_id, authority)
        .with_for_update(of=p, key_share=True)).mappings().one_or_none()
    if project is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    risk = c.execute(sa.select(r).where(r.c.id == risk_id, r.c.project_id == project.id)
        .with_for_update(key_share=True)).mappings().one_or_none()
    if risk is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return project, risk


def _resolution_open(p, risk) -> bool:
    return (p.stage in WRITABLE and risk.status in ('open', 'mitigated') and risk.closed_at is None
            and risk.handoff_to_id is None)


def _writable(p, risk, expected):
    if risk.revision != expected:
        raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
    if not _resolution_open(p, risk):
        raise Failure(422, 'RISK_REGISTER_LOCKED', project_id=p.id)


def _acceptance_rows(c, risk, receiver, eid):
    t = acceptances.c
    rows = c.execute(sa.select(acceptances).where(t.project_id == risk.project_id, t.risk_id == risk.id,
        t.risk_revision == risk.revision, t.receiver_id == receiver, t.evidence_id == eid)).mappings()
    return {row.kind: row for row in rows}


def _same_proof(row, proof):
    return (row.evidence_id, row.evidence_revision, row.evidence_sha256) == (proof.id, proof.revision, proof.sha256)


def _external_verified(details, scope, risk, revision, receiver, proof, data):
    # The reviewer checks who actually accepted. The quote must be in the immutable original.
    if proof.mime_type != 'application/pdf' or len(data) > 1024 * 1024:
        return False
    if not risk.created_at <= details.acceptedAt <= proof.verified_at:
        return False
    normalized_scope = ' '.join(scope.split())
    excerpt = ' '.join(details.acceptanceExcerpt.split())
    clause = f'I explicitly accept risk {risk.id} revision {revision} as receiver {receiver} for scope: {normalized_scope}'
    if not normalized_scope or excerpt != clause:
        return False
    paragraph = re.compile(r'^[^\S\n]*' + r'\s+'.join(map(re.escape, clause.split())) + r'[^\S\n]*$', re.MULTILINE)
    try:
        document = PdfReader(BytesIO(data), strict=True)
        if document.is_encrypted or not 1 <= len(document.pages) <= 5:
            return False
        return any(paragraph.search(page.extract_text()) for page in document.pages)
    except Exception:
        return False


def _acceptance_valid(c, row, p, risk, revision, receiver, proof, data, *, pending=False):
    if row is None or (row.project_id, row.risk_id, row.risk_revision, row.receiver_id) != (p.id, risk.id, revision, receiver):
        return False
    if (not _same_proof(row, proof) or row.risk_fingerprint != _risk_subject(risk, revision)
            or row.fingerprint != _receipt_hash('risk-acceptance-v1', row, _proof_binding(proof))
            or not row.scope or not row.scope.strip()):
        return False
    if row.kind == 'receiver_confirmed' and not pending:
        if row.actor_id != receiver or row.reviewer_assignment_id or not row.request_receipt_id or row.verification is not None:
            return False
        request = c.execute(sa.select(acceptances).where(acceptances.c.id == row.request_receipt_id)).mappings().one_or_none()
        return (request is not None and request.scope == row.scope and request.created_at <= row.created_at
                and _acceptance_valid(c, request, p, risk, revision, receiver, proof, data, pending=True))
    if (row.kind != ('requested' if pending else 'reviewer_verified') or row.request_receipt_id is not None):
        return False
    assignment = _reviewer(c, p, risk, row.actor_id, receiver, proof)
    if assignment is None or assignment.id != row.reviewer_assignment_id or assignment.accepted_at > row.created_at:
        return False
    if pending:
        return row.verification is None
    try:
        verification = ExternalVerification.model_validate(row.verification)
    except ValidationError:
        return False
    return _external_verified(verification, row.scope, risk, revision, receiver, proof, data)


def _record(c, a, op, key, rid, row, signature, result, revision, before):
    body = result.model_dump(mode='json', exclude_none=True)
    record_receipt(c, a, key, op, rid, row.risk_id, row.project_id, signature, body, fingerprint(body), before)
    c.execute(db.outbox_events.insert().values(dedupe_key=rid, event_type=op, status='pending', payload={
        'projectId': str(row.project_id), 'riskId': str(row.risk_id), 'receiptId': str(row.id),
        'riskRevision': revision, 'kind': row.kind,
    }))
    return result


def _insert_receipt(c, table, *, p, risk, a, rid, proof, assignment, receiver, now, domain, bindings, **fields):
    values = dict(id=uuid4(), project_id=p.id, risk_id=risk.id, actor_id=a.person, receiver_id=receiver,
        reviewer_assignment_id=assignment.id if assignment else None, request_id=UUID(rid), created_at=now,
        evidence_id=proof.id, evidence_sha256=proof.sha256, evidence_revision=proof.revision, **fields)
    values['fingerprint'] = _receipt_hash(domain, values, _proof_binding(proof), *bindings)
    return c.execute(table.insert().values(**values).returning(table)).mappings().one()


@router.post(PATH + '/handoff/acceptance', operation_id=ACCEPTANCE, response_model_exclude_none=True, openapi_extra=HUMAN)
def handoff_acceptance(riskId: UUID, command: AcceptanceCommand, authorization: Auth, key: Key, expected: Revision) -> Result[Acceptance]:
    revision = int(expected)
    receiver = command.receiverPersonId
    eid = UUID(command.acceptanceEvidenceRef.removeprefix('evidence:'))
    signature = fingerprint([str(riskId), revision, command.model_dump(mode='json')])

    def write(c, a, rid):
        confirming = isinstance(command, AcceptanceConfirm)
        if confirming and a.person != receiver:
            raise Failure(403, 'RISK_RECEIVER_REQUIRED')
        p, risk = _project_and_risk(c, a, riskId, confirming=eid if confirming else None)
        assignment = None if confirming else _reviewer(c, p, risk, a.person, receiver)
        if not confirming and assignment is None:
            raise Failure(403, 'INDEPENDENT_REVIEWER_REQUIRED', project_id=p.id)
        if not _receiver(c, p, risk, receiver):
            raise Failure(422, 'INVALID_RISK_RECEIVER', project_id=p.id)
        prior = replay(c, a, ACCEPTANCE, key, signature, Result[Acceptance])
        if prior:
            return prior
        _writable(p, risk, revision)
        rows = _acceptance_rows(c, risk, receiver, eid)
        request = rows.get('requested')
        if confirming and request is None:
            raise Failure(422, 'RISK_HANDOFF_REQUEST_REQUIRED', project_id=p.id)
        if any(row.scope != command.acceptanceScope for row in rows.values()):
            raise Failure(409, 'RISK_ACCEPTANCE_SCOPE_MISMATCH', project_id=p.id)
        if 'receiver_confirmed' in rows or 'reviewer_verified' in rows or (isinstance(command, AcceptanceRequest) and request):
            raise Failure(409, 'RISK_ACCEPTANCE_ALREADY_RECORDED', project_id=p.id)
        proof, data = _proof(c, p, risk, eid)
        if not confirming and a.person in (proof.uploaded_by, proof.owner_id):
            raise Failure(403, 'INDEPENDENT_REVIEWER_REQUIRED', project_id=p.id)
        verification = None
        request_receipt_id = None
        if confirming:
            if not _acceptance_valid(c, request, p, risk, revision, receiver, proof, data, pending=True):
                raise Failure(409, 'RISK_HANDOFF_REQUEST_STALE', project_id=p.id)
            kind: AcceptanceKind = 'receiver_confirmed'
            request_receipt_id = request.id
        elif isinstance(command, AcceptanceVerify):
            details = ExternalVerification.model_validate(command.model_dump(include=set(ExternalVerification.model_fields)))
            if not _external_verified(details, command.acceptanceScope, risk, revision, receiver, proof, data):
                raise Failure(422, 'EXPLICIT_RISK_ACCEPTANCE_EVIDENCE_REQUIRED', project_id=p.id)
            verification = details.model_dump(mode='json')
            kind = 'reviewer_verified'
        else:
            kind = 'requested'
        subject = _risk_subject(risk, revision)
        row = _insert_receipt(c, acceptances, p=p, risk=risk, a=a, rid=rid, proof=proof, assignment=assignment,
            receiver=receiver, now=c.scalar(sa.select(sa.func.statement_timestamp())), domain='risk-acceptance-v1', bindings=(),
            kind=kind, risk_revision=revision, risk_fingerprint=subject, scope=command.acceptanceScope,
            request_receipt_id=request_receipt_id, verification=verification)
        result = Result[Acceptance](data=Acceptance(acceptanceId=row.id, projectId=p.id, riskId=risk.id,
            riskRevision=revision, receiverPersonId=receiver, actorPersonId=a.person, acceptanceScope=row.scope,
            acceptanceEvidenceRef=f'evidence:{eid}', evidenceSha256=row.evidence_sha256, evidenceRevision=row.evidence_revision,
            kind=row.kind, createdAt=row.created_at), meta=meta(ACCEPTANCE, [f'risk:{risk.id}@{revision}', f'evidence:{eid}',
                f'risk-acceptance:{row.id}']))
        return _record(c, a, ACCEPTANCE, key, rid, row, signature, result, revision, subject)

    return run(authorization, ACCEPTANCE, write)


def _resolve_write(auth, op, risk_id, command: Close | Handoff, key, revision):
    if isinstance(command, Handoff):
        receiver, ref = command.receiverPersonId, command.acceptanceEvidenceRef
    else:
        receiver, ref = None, command.resolutionEvidenceRef
    eid = UUID(ref.removeprefix('evidence:'))
    signature = fingerprint([str(risk_id), revision, command.model_dump(mode='json')])

    def write(c, a, rid):
        p, risk = _project_and_risk(c, a, risk_id)
        assignment = _reviewer(c, p, risk, a.person, receiver)
        if assignment is None:
            raise Failure(403, 'INDEPENDENT_REVIEWER_REQUIRED', project_id=p.id)
        prior = replay(c, a, op, key, signature, Result[Resolution])
        if prior:
            return prior
        _writable(p, risk, revision)
        if revision == 2**63 - 1:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        if receiver is not None and not _receiver(c, p, risk, receiver):
            raise Failure(422, 'INVALID_RISK_RECEIVER', project_id=p.id)
        proof, data = _proof(c, p, risk, eid)
        if a.person in (proof.uploaded_by, proof.owner_id):
            raise Failure(403, 'INDEPENDENT_REVIEWER_REQUIRED', project_id=p.id)
        acceptance = None
        if receiver is not None:
            candidates = _acceptance_rows(c, risk, receiver, eid)
            acceptance = candidates.get('receiver_confirmed') or candidates.get('reviewer_verified')
            if not _acceptance_valid(c, acceptance, p, risk, revision, receiver, proof, data):
                raise Failure(422, 'RISK_ACCEPTANCE_REQUIRED', project_id=p.id)
        status = 'transferred' if receiver is not None else 'closed'
        now = c.scalar(sa.select(sa.func.statement_timestamp()))
        t = db.risks
        updated = c.execute(t.update().where(t.c.id == risk.id).values(status=status, handoff_to_id=receiver,
            closed_at=now if receiver is None else None, revision=revision + 1, updated_at=now).returning(t)).mappings().one()
        row = _insert_receipt(c, resolutions, p=p, risk=risk, a=a, rid=rid, proof=proof, assignment=assignment,
            receiver=receiver, now=now, domain='risk-resolution-v1',
            bindings=(_json_fields(updated), acceptance.fingerprint if acceptance is not None else None),
            kind=status, prior_revision=revision, revision=updated.revision, acceptance_id=acceptance.id if acceptance is not None else None)
        result = Result[Resolution](data=Resolution(**view(updated).model_dump(), resolutionId=row.id, reviewerPersonId=a.person,
            receiverPersonId=receiver, acceptanceId=row.acceptance_id, evidenceRef=f'evidence:{eid}', evidenceSha256=proof.sha256,
            evidenceRevision=proof.revision, closedAt=updated.closed_at), meta=meta(op, [f'risk:{risk.id}@{updated.revision}',
                f'evidence:{eid}', f'risk-resolution:{row.id}']))
        return _record(c, a, op, key, rid, row, signature, result, updated.revision, fingerprint(_json_fields(risk)))

    return run(auth, op, write)


@router.post(PATH + '/close', operation_id=CLOSE, response_model_exclude_none=True, openapi_extra=HUMAN_REVIEWER)
def close(riskId: UUID, command: Close, authorization: Auth, key: Key, expected: Revision) -> Result[Resolution]:
    return _resolve_write(authorization, CLOSE, riskId, command, key, int(expected))


@router.post(PATH + '/handoff', operation_id=HANDOFF, response_model_exclude_none=True, openapi_extra=HUMAN_REVIEWER)
def handoff(riskId: UUID, command: Handoff, authorization: Auth, key: Key, expected: Revision) -> Result[Resolution]:
    return _resolve_write(authorization, HANDOFF, riskId, command, key, int(expected))


def resolve_risk(c, a, p, risk):
    """Read the effective receipt, never infer resolution from a terminal status alone."""
    if risk.project_id != p.id or risk.status not in ('closed', 'transferred'):
        return None
    projects, risks = db.projects, db.risks
    project = c.execute(sa.select(projects).where(projects.c.id == p.id, visible(a))
        .with_for_update(read=True)).mappings().one_or_none()
    current = c.execute(sa.select(risks).where(risks.c.id == risk.id, risks.c.project_id == p.id)
        .with_for_update(read=True)).mappings().one_or_none() if project is not None else None
    if current is None or _json_fields(current) != _json_fields(risk):
        return None
    t = resolutions.c
    row = c.execute(sa.select(resolutions).where(t.project_id == project.id, t.risk_id == current.id,
        t.revision == current.revision)).mappings().one_or_none()
    if row is None or row.kind != current.status or row.prior_revision + 1 != current.revision:
        return None
    receiver = row.receiver_id
    if row.kind == 'closed':
        if receiver is not None or row.acceptance_id is not None or current.handoff_to_id is not None or current.closed_at != row.created_at:
            return None
    elif (current.closed_at is not None or current.handoff_to_id != receiver or row.acceptance_id is None
            or not _receiver(c, project, current, receiver)):
        return None
    try:
        proof, data = _proof(c, project, current, row.evidence_id)
    except Failure:
        return None
    reviewer = _reviewer(c, project, current, row.actor_id, receiver, proof)
    if reviewer is None or reviewer.id != row.reviewer_assignment_id or reviewer.accepted_at > row.created_at or not _same_proof(row, proof):
        return None
    acceptance = None
    if row.kind == 'transferred':
        acceptance = c.execute(sa.select(acceptances).where(acceptances.c.id == row.acceptance_id)).mappings().one_or_none()
        if not _acceptance_valid(c, acceptance, project, current, row.prior_revision, receiver, proof, data):
            return None
        if acceptance.created_at > row.created_at:
            return None
    return row if row.fingerprint == _receipt_hash('risk-resolution-v1', row, _proof_binding(proof),
        _json_fields(current), acceptance.fingerprint if acceptance is not None else None) else None


def _read_context(c, a, risk_id):
    if a.kind != 'human':
        raise Failure(403, 'FORBIDDEN')
    p, r, t = db.projects, db.risks, acceptances.c
    nominated = sa.exists(sa.select(1).where(t.project_id == p.c.id, t.risk_id == r.c.id,
        t.risk_revision == r.c.revision, t.receiver_id == a.person, t.kind == 'requested'))
    project = c.execute(sa.select(p).join(r, r.c.project_id == p.c.id).where(r.c.id == risk_id,
        p.c.org_id == a.org, sa.or_(visible(a), nominated)).with_for_update(read=True, of=p)).mappings().one_or_none()
    if project is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    risk = c.execute(sa.select(r).where(r.c.id == risk_id, r.c.project_id == project.id)
        .with_for_update(read=True)).mappings().one_or_none()
    if risk is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    # Recheck the same project visibility policy after acquiring the project lock.
    member = c.scalar(sa.select(visible(a)).select_from(p).where(p.c.id == project.id))
    return project, risk, bool(member)


def _current_candidates(c, risk, receiver=None):
    t = acceptances.c
    positive = acceptances.alias('positive').c
    handled = sa.exists(sa.select(1).where(positive.project_id == t.project_id, positive.risk_id == t.risk_id,
        positive.risk_revision == t.risk_revision, positive.receiver_id == t.receiver_id, positive.evidence_id == t.evidence_id,
        positive.kind.in_(('receiver_confirmed', 'reviewer_verified'))))
    stmt = sa.select(acceptances).where(t.project_id == risk.project_id, t.risk_id == risk.id, t.risk_revision == risk.revision,
        sa.or_(t.kind != 'requested', ~handled))
    if receiver is not None:
        stmt = stmt.where(t.receiver_id == receiver, t.kind == 'requested')
    # Positive records take priority in a bounded result; a replaced request is never actionable.
    rows = c.execute(stmt.order_by(t.kind == 'requested', t.created_at.desc(), t.id).limit(ACCEPTANCE_LIMIT + 1)).mappings().all()
    return rows[:ACCEPTANCE_LIMIT], len(rows) > ACCEPTANCE_LIMIT


def _acceptance_evidence(row):
    return dict(acceptanceId=row.id, riskId=row.risk_id, riskRevision=row.risk_revision,
        receiverPersonId=row.receiver_id, acceptanceScope=row.scope, acceptanceEvidenceRef=f'evidence:{row.evidence_id}',
        evidenceSha256=row.evidence_sha256, evidenceRevision=row.evidence_revision, createdAt=row.created_at)


def _acceptance_state(row, *, can_confirm=False, can_handoff=False):
    return AcceptanceState(**_acceptance_evidence(row), kind=row.kind,
        status='pending' if row.kind == 'requested' else 'accepted', canConfirm=can_confirm, canHandoff=can_handoff,
        projectId=row.project_id, actorPersonId=row.actor_id)


def _read_resolution(c, a, risk_id):
    p, risk, member = _read_context(c, a, risk_id)
    writable = _resolution_open(p, risk)
    terminal = risk.status in ('closed', 'transferred')
    if not member and not writable:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    reviewer = member and writable and _reviewer(c, p, risk, a.person) is not None
    can_accept = ACCEPTANCE in a.actions
    can_finalize = reviewer and risk.revision < 2**63 - 1
    receipt = resolve_risk(c, a, p, risk) if member and terminal else None
    items: list[AcceptanceState] = []
    nominations: list[Nomination] = []
    truncated = False
    resolution = None
    if receipt is not None:
        resolution = Resolution(**view(risk).model_dump(), resolutionId=receipt.id, reviewerPersonId=receipt.actor_id,
            receiverPersonId=receipt.receiver_id, acceptanceId=receipt.acceptance_id, evidenceRef=f'evidence:{receipt.evidence_id}',
            evidenceSha256=receipt.evidence_sha256, evidenceRevision=receipt.evidence_revision, closedAt=risk.closed_at)
        if receipt.acceptance_id is not None:
            accepted = c.execute(sa.select(acceptances).where(acceptances.c.id == receipt.acceptance_id)).mappings().one()
            items.append(_acceptance_state(accepted))
    elif not terminal:
        rows, truncated = _current_candidates(c, risk, None if member else a.person)
        for row in rows:
            if not _receiver(c, p, risk, row.receiver_id):
                continue
            try:
                proof, data = _proof(c, p, risk, row.evidence_id)
            except Failure:
                continue
            pending = row.kind == 'requested'
            if not _acceptance_valid(c, row, p, risk, risk.revision, row.receiver_id, proof, data, pending=pending):
                continue
            confirm = pending and writable and can_accept and row.receiver_id == a.person
            handoff = (not pending and can_finalize and HANDOFF in a.actions
                       and a.person not in (row.receiver_id, proof.uploaded_by, proof.owner_id))
            if member:
                items.append(_acceptance_state(row, can_confirm=confirm, can_handoff=handoff))
            elif confirm:
                nominations.append(Nomination(**_acceptance_evidence(row)))

    if not member and not nominations:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    refs = [f'risk:{risk.id}@{risk.revision}', *(f'risk-acceptance:{item.acceptanceId}' for item in (items if member else nominations))]
    state: ProjectResolutionState | ReceiverResolutionState
    if member:
        if resolution is not None:
            refs.append(f'risk-resolution:{resolution.resolutionId}')
        status = ('resolved' if resolution is not None else 'accepted' if any(i.status == 'accepted' for i in items)
                  else 'pending' if items else 'unresolved')
        state = ProjectResolutionState(risk=view(risk), status=status, resolution=resolution, acceptances=items,
            acceptancesTruncated=truncated, canClose=can_finalize and CLOSE in a.actions,
            canRequestAcceptance=reviewer and can_accept, canVerifyAcceptance=reviewer and can_accept)
    else:
        state = ReceiverResolutionState(risk=RiskRevision(riskId=risk.id, revision=risk.revision),
            acceptances=nominations, acceptancesTruncated=truncated)
    return Result[ResolutionState](data=state, meta=meta(GET, refs))


@router.get(PATH + '/resolution', operation_id=GET, response_model_exclude_none=True, openapi_extra=HUMAN)
def get_resolution(riskId: UUID, authorization: Auth) -> Result[ResolutionState]:
    return run(authorization, GET, lambda c, a, _: _read_resolution(c, a, riskId))
