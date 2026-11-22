"""Independent closing-cost evidence, enabled only in isolated synthetic schemas."""
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from unicodedata import category, normalize
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field
from sqlalchemy.dialects.postgresql import JSONB

from . import evidence_storage, schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import RULE_VERSION, Result, Strict
from .costs import DisplayedAmount, DisplayedNet, Kind, latest_plan, plans
from .identity import Failure, visible
from .materials import replay
from .projects import fingerprint, meta, record_receipt, run
from .requirement_baselines import active_member

GET = 'human_cost_reconciliation_get'
CREATE = 'human_cost_reconciliation_create'
POLICY_VERSION = 'synthetic-cost-reconciliation-v1'
KINDS: tuple[Kind, Kind] = ('money', 'effort')
PATH = '/projects/{projectId}/cost-reconciliations'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
router = APIRouter(prefix='/api/v1', tags=['Cost reconciliation'])

grants = sa.table('cost_reconciliation_grants', sa.column('id', sa.Uuid),
    sa.column('project_id', sa.Uuid), sa.column('person_id', sa.Uuid),
    *(sa.column(k, sa.DateTime(timezone=True)) for k in ('granted_at', 'expires_at', 'revoked_at')))
receipts = sa.table('cost_reconciliations',
    *(sa.column(k, sa.Uuid) for k in ('id', 'project_id', 'signed_by', 'grant_id', 'request_id')),
    sa.column('revision', sa.BigInteger), sa.column('signed_at', sa.DateTime(timezone=True)),
    sa.column('covered_through', sa.DateTime(timezone=True)), sa.column('rule_version'),
    sa.column('cost_hash'), sa.column('receipt_hash'), sa.column('basis', JSONB), sa.column('lines', JSONB))

Hash = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
EvidenceRef = Annotated[str, Field(pattern=r'^evidence:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')]
Attribution = Literal['estimate', 'execution', 'scope']
VarianceRate = Annotated[str, Field(pattern=r'^-?(0|[1-9][0-9]*)\.[0-9]{8}$')]


class Create(Strict):
    costHash: Hash
    evidenceRefs: Annotated[list[EvidenceRef], Field(min_length=1, max_length=2)]
    overrunReasons: dict[Kind, Attribution] = Field(default_factory=dict)


class SourceLine(Strict):
    type: Kind
    sourceRef: str = Field(min_length=3, max_length=256, pattern=r'\S')
    netActual: str = Field(pattern=r'^(0|[1-9][0-9]{0,29})(\.[0-9]{1,2})?$')


class Source(Strict):
    schemaVersion: int = Field(strict=True, ge=1, le=1)
    projectId: UUID
    coveredThrough: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d:[0-5]\d'
                                      r'(\.\d{1,6})?(Z|[+-]([01]\d|2[0-3]):[0-5]\d)$')
    lines: Annotated[list[SourceLine], Field(min_length=1, max_length=2)]


class Proof(Strict):
    evidenceRef: EvidenceRef
    linkedResourceType: Literal['project']
    linkedResourceId: UUID
    linkRevision: int
    sha256: Hash
    verifiedBy: UUID
    verifiedAt: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$',
                            json_schema_extra={'format': 'date-time'})


class Variance(Strict):
    budget: DisplayedAmount | None = None
    difference: DisplayedNet | None = None
    rate: VarianceRate | None = None
    sourceRefs: list[str]


class _Line(Strict):
    actual: DisplayedAmount
    sourceRef: str
    sourceNetActual: DisplayedAmount
    difference: DisplayedNet
    forecastRemaining: DisplayedAmount
    forecastSourceRefs: list[str]
    initial: Variance
    current: Variance
    proof: Proof
    overrunReason: Attribution | None = None


class MoneyLine(_Line):
    type: Literal['money']
    unit: Literal['CNY']


class EffortLine(_Line):
    type: Literal['effort']
    unit: Literal['person_day']


class Receipt(Strict):
    receiptId: UUID
    projectId: UUID
    revision: int
    signedBy: UUID
    signedAt: datetime
    grantId: UUID
    coveredThrough: datetime
    ruleVersion: str
    costHash: Hash
    receiptHash: Hash
    lines: tuple[MoneyLine, EffortLine]
    policyStatus: Literal['POLICY_PENDING'] = 'POLICY_PENDING'


class State(Strict):
    projectId: UUID
    revision: int
    costHash: Hash
    canCreate: bool
    current: Receipt | None
    policyStatus: Literal['POLICY_PENDING'] = 'POLICY_PENDING'


@dataclass(frozen=True)
class Basis:
    payload: dict
    complete: bool
    latest_posting_time: datetime | None
    participants: frozenset[UUID]

    @property
    def digest(self):
        return fingerprint(self.payload)


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _amount(value: Decimal) -> str:
    return format(value if value else Decimal(0), '.2f')


def _current_ref(row):
    return f"cost-entry:{row['id']}@{row['revision']}"


def _initial_ref(row):
    return f"cost-baseline-history:{row['id']}@{row['historySeq']}"


def _basis(c, p, now) -> Basis:
    t = db.cost_entries
    current = [dict(id=str(r.id), revision=r.revision, baselineVersion=r.baseline_version, type=r.type,
                    category=r.category, periodStart=r.period_start.isoformat(), periodEnd=r.period_end.isoformat(),
                    budget=_amount(r.budget), forecastRemaining=_amount(r.forecast_remaining), sourceRef=r.source_ref)
               for r in c.execute(sa.select(t).where(t.c.project_id == p.id, t.c.is_current_baseline,
                    t.c.category != 'posting_ledger').order_by(t.c.id)).mappings()]
    # Use the first active history row for each v1 entry, including subsequently retired entries.
    initial = [dict(id=str(r.entry_id), historySeq=r.seq, type=r.type, category=r.category,
                    periodStart=r.period_start.isoformat(), periodEnd=r.period_end.isoformat(),
                    budget=_amount(r.budget), sourceRef=r.source_ref)
               for r in c.execute(sa.text('''SELECT DISTINCT ON (h.entry_id)
                    h.entry_id, h.seq, h.type, h.budget, e.category, e.period_start, e.period_end, e.source_ref
                    FROM cost_baseline_history h JOIN cost_entries e
                    ON e.id=h.entry_id AND e.project_id=h.project_id
                    WHERE h.project_id=:pid AND h.is_current AND h.effective_at<=:now
                    AND e.baseline_version=1 AND e.category<>'posting_ledger'
                    ORDER BY h.entry_id, h.effective_at, h.seq'''), {'pid': p.id, 'now': now}).mappings()]
    plan = c.execute(latest_plan(p.id).where(plans.c.effective_at <= now)).mappings().one_or_none()
    plan_input = (dict(revision=plan.revision, moneyRemaining=None if plan.money_remaining is None else _amount(plan.money_remaining),
                       effortRemaining=None if plan.effort_remaining is None else _amount(plan.effort_remaining))
                  if plan else None)
    ledger = []
    participants: set[UUID] = set()
    latest_time = None
    l = db.cost_postings
    for r in c.execute(sa.select(l).where(l.c.project_id == p.id).order_by(l.c.id)).mappings():
        included = (r.status == 'verified' and r.occurred_at is not None and r.verified_at is not None
                    and r.occurred_at <= r.verified_at <= now and r.verified_by is not None
                    and r.verified_by != r.created_by and bool(r.source_fingerprint))
        ledger.append(dict(id=str(r.id), status=r.status, type=r.type, costEntryId=str(r.cost_entry_id), period=r.period,
            amountSigned=_amount(r.amount_signed), sourceRef=r.source_ref, sourceFingerprint=r.source_fingerprint,
            postingOfId=str(r.posting_of_id) if r.posting_of_id else None,
            occurredAt=_utc(r.occurred_at) if r.occurred_at else None, verifiedAt=_utc(r.verified_at) if r.verified_at else None,
            createdBy=str(r.created_by), verifiedBy=str(r.verified_by) if r.verified_by else None, includedAtCutoff=included))
        if included:
            participants.update((r.created_by, r.verified_by))
            latest_time = max(latest_time, r.verified_at) if latest_time else r.verified_at
    forecasts = {}
    with localcontext() as ctx:
        ctx.prec = 64
        for kind in KINDS:
            planned = plan[kind + '_remaining'] if plan else None
            rows = [row for row in current if row['type'] == kind]
            forecasts[kind] = dict(value=_amount(planned if planned is not None else
                sum((Decimal(row['forecastRemaining']) for row in rows), Decimal(0))),
                sourceRefs=[f'cost-plan:{p.id}@{plan.revision}'] if planned is not None else [_current_ref(row) for row in rows])
    payload = dict(schemaVersion=1, ruleVersion=RULE_VERSION, policyVersion=POLICY_VERSION,
        orgId=str(p.org_id), projectId=str(p.id), initial=initial, current=current, plan=plan_input,
        forecasts=forecasts, postings=ledger)
    return Basis(payload, all(r['includedAtCutoff'] for r in ledger), latest_time, frozenset(participants))


def _variance(rows, actual, *, initial=False):
    ref = _initial_ref if initial else _current_ref
    if not rows:
        return Variance(sourceRefs=[])
    budget = sum((Decimal(r['budget']) for r in rows), Decimal(0))
    difference = actual - budget
    return Variance(budget=_amount(budget), difference=_amount(difference),
                    rate=format(difference / budget, '.8f') if budget else None,
                    sourceRefs=[ref(row) for row in rows])


def _synthetic(c):
    from .requirement_delivery import synthetic_confirmation
    return synthetic_confirmation(c)


def _grant(c, p, person, now, grant_id=None, signed_at=None):
    if person in (p.pm_id, p.initiator_id, p.sponsor_id) or not any(
            active_member(c, p, person, role) for role in ('reviewer', 'member')):
        return None
    g, n, roles = grants.c, db.persons.c, db.user_roles.c
    stmt = sa.select(g.id).select_from(grants.join(db.persons, n.id == g.person_id)
        .join(db.user_roles, roles.user_id == n.user_id)).where(g.project_id == p.id, g.person_id == person,
        roles.role == 'finance_verifier', g.granted_at <= (signed_at or now), g.revoked_at.is_(None),
        sa.or_(g.expires_at.is_(None), g.expires_at > now))
    if grant_id is not None:
        stmt = stmt.where(g.id == grant_id)
    return c.scalar(stmt.order_by(g.granted_at.desc(), g.id).limit(1).with_for_update(read=True, of=(grants, db.user_roles)))


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate source field')
        result[key] = value
    return result


def _source_ref(value: str) -> str:
    ref = ' '.join(normalize('NFKC', value).split()).casefold()
    internal = ('pmo:', 'project:', 'cost-entry:', 'cost-posting:', 'cost-plan:', 'cost-reconciliation:', 'evidence:', 'api/v1/')
    if (len(ref) < 3 or any(category(char).startswith('C') for char in value)
            or ref.startswith(internal) or '/api/v1/' in ref):
        raise ValueError('external source identifier required')
    return ref


def _read_source(data: bytes):
    if len(data) > 16_384:
        raise ValueError('source exceeds size limit')
    sheet = Source.model_validate(json.loads(data.decode('utf-8'), object_pairs_hook=_unique_keys))
    through = datetime.fromisoformat(sheet.coveredThrough).astimezone(UTC)
    return sheet, through


def _lines(c, a, p, signer, refs, reasons, basis, now):
    from .evidence import verified_link

    def fail(code, status=422):
        return Failure(status, code, project_id=p.id)

    if not basis.complete:
        raise fail('UNVERIFIED_OR_FUTURE_POSTINGS')
    if signer in basis.participants:
        raise fail('INDEPENDENT_RECONCILER_REQUIRED', 403)
    if len(set(refs)) != len(refs):
        raise fail('RECONCILIATION_SOURCE_COVERAGE')
    cutoff = None
    external: dict[Kind, tuple[SourceLine, Proof]] = {}
    source_ids = set()
    for ref in sorted(refs):
        try:
            row = verified_link(c, a, ref, p.id, 'project', p.id, shared=True)
            if row.mime_type != 'application/json':
                raise ValueError('reconciliation needs a JSON original')
            data = evidence_storage.checked_bytes(row.storage_key, row.sha256, row.size_bytes)
            sheet, covered = _read_source(data)
            keys = [_source_ref(line.sourceRef) for line in sheet.lines]
        except Failure as exc:
            raise fail('RECONCILIATION_EVIDENCE_UNTRUSTED') from exc
        except (ValueError, RecursionError, OverflowError) as exc:
            raise fail('INVALID_RECONCILIATION_SOURCE') from exc
        if row.uploaded_by == signer:
            raise fail('INDEPENDENT_RECONCILER_REQUIRED', 403)
        if (sheet.projectId != p.id or covered > row.verified_at or row.verified_at > now
                or (cutoff is not None and cutoff != covered)):
            raise fail('RECONCILIATION_SOURCE_COVERAGE')
        cutoff = covered
        proof = Proof(evidenceRef=ref, linkedResourceType='project', linkedResourceId=p.id, linkRevision=row.revision,
                      sha256=row.sha256, verifiedBy=row.verified_by, verifiedAt=_utc(row.verified_at))
        for line, key in zip(sheet.lines, keys):
            if line.type in external or key in source_ids:
                raise fail('RECONCILIATION_SOURCE_COVERAGE')
            source_ids.add(key)
            external[line.type] = (line, proof)
    if set(external) != set(KINDS) or cutoff is None or (basis.latest_posting_time and basis.latest_posting_time > cutoff):
        raise fail('RECONCILIATION_SOURCE_COVERAGE')
    lines = []
    with localcontext() as ctx:
        ctx.prec = 64
        for kind in KINDS:
            source, proof = external[kind]
            actual = sum((Decimal(r['amountSigned']) for r in basis.payload['postings']
                          if r['type'] == kind and r['includedAtCutoff']), Decimal(0))
            source_net = Decimal(source.netActual)
            if actual < 0 or actual != source_net:
                raise fail('RECONCILIATION_TOTAL_MISMATCH')
            initial = _variance([r for r in basis.payload['initial'] if r['type'] == kind], actual, initial=True)
            current = _variance([r for r in basis.payload['current'] if r['type'] == kind], actual)
            reason = reasons.get(kind)
            if current.difference is not None and Decimal(current.difference) > 0 and reason is None:
                raise fail('RECONCILIATION_OVERRUN_REASON_REQUIRED')
            forecast = basis.payload['forecasts'][kind]
            values = dict(actual=_amount(actual), sourceRef=source.sourceRef.strip(), sourceNetActual=_amount(source_net),
                difference=_amount(actual - source_net), forecastRemaining=forecast['value'], forecastSourceRefs=forecast['sourceRefs'],
                initial=initial, current=current, proof=proof, overrunReason=reason)
            line = MoneyLine(type='money', unit='CNY', **values) if kind == 'money' else EffortLine(type='effort', unit='person_day', **values)
            lines.append(line.model_dump(mode='json', exclude_none=True))
    return cutoff, lines


def _receipt_hash(project_id, revision, signed_by, grant_id, signed_at, cutoff, cost_hash, lines, request_id):
    return fingerprint(dict(projectId=str(project_id), revision=revision, signedBy=str(signed_by), grantId=str(grant_id),
        signedAt=_utc(signed_at), coveredThrough=_utc(cutoff), ruleVersion=RULE_VERSION, costHash=cost_hash,
        lines=lines, requestId=str(request_id)))


def _latest(c, pid):
    return c.execute(sa.select(receipts).where(receipts.c.project_id == pid)
        .order_by(receipts.c.revision.desc()).limit(1)).mappings().one_or_none()


def _refs(row):
    basis = row.basis
    return list(dict.fromkeys([f'cost-reconciliation:{row.id}@{row.revision}',
        *(item['proof']['evidenceRef'] for item in row.lines),
        *(_initial_ref(item) for item in basis['initial']), *(_current_ref(item) for item in basis['current']),
        *(ref for forecast in basis['forecasts'].values() for ref in forecast['sourceRefs']),
        *(f"cost-posting:{item['id']}@2" for item in basis['postings'])]))


def _current(c, a, p, row, basis, now):
    if (row is None or not basis.complete or row.rule_version != RULE_VERSION or row.cost_hash != basis.digest
            or row.basis != basis.payload or row.signed_at > now
            or not _grant(c, p, row.signed_by, now, grant_id=row.grant_id, signed_at=row.signed_at)):
        return None
    try:
        saved = _view(row).lines
        cutoff, lines = _lines(c, a, p, row.signed_by, {line.proof.evidenceRef for line in saved},
            {line.type: line.overrunReason for line in saved if line.overrunReason is not None}, basis, now)
    except (Failure, ValueError, TypeError):
        return None
    if (lines != row.lines or cutoff != row.covered_through or row.receipt_hash != _receipt_hash(
            row.project_id, row.revision, row.signed_by, row.grant_id, row.signed_at, cutoff, row.cost_hash,
            lines, row.request_id)):
        return None
    return row, _refs(row)


def current_reconciliation(c, a, p):
    """Return (immutable receipt row, source refs) or None; caller holds the project lock."""
    if not _synthetic(c):
        return None
    row = _latest(c, p.id)
    if row is None:
        return None
    now = c.scalar(sa.select(sa.func.clock_timestamp()))
    return _current(c, a, p, row, _basis(c, p, now), now)


def _project(c, a, pid, *, write):
    if a.kind != 'human':
        raise Failure(403, 'FORBIDDEN')
    p = db.projects
    row = c.execute(sa.select(p).where(p.c.id == pid, visible(a))
        .with_for_update(read=not write)).mappings().one_or_none()
    if row is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return row


def _view(row):
    return Receipt(receiptId=row.id, projectId=row.project_id, revision=row.revision, signedBy=row.signed_by,
        signedAt=row.signed_at, grantId=row.grant_id, coveredThrough=row.covered_through, ruleVersion=row.rule_version,
        costHash=row.cost_hash, receiptHash=row.receipt_hash, lines=row.lines)


@router.get(PATH, operation_id=GET, openapi_extra=HUMAN)
def get(projectId: UUID, authorization: Auth) -> Result[State]:
    def read(c, a, _):
        p = _project(c, a, projectId, write=False)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        basis = _basis(c, p, now)
        enabled = _synthetic(c)
        row = _latest(c, p.id) if enabled else None
        current = _current(c, a, p, row, basis, now) if row else None
        can_create = (enabled and p.stage == 'delivering' and basis.complete and a.person not in basis.participants
                      and bool(_grant(c, p, a.person, now)))
        data = State(projectId=p.id, revision=row.revision if row else 1, costHash=basis.digest, canCreate=can_create,
                     current=_view(current[0]) if current else None)
        return Result[State](data=data, meta=meta(GET, current[1] if current else [f'project:{p.id}']))
    return run(authorization, GET, read)


@router.post(PATH, operation_id=CREATE, openapi_extra=HUMAN)
def create(projectId: UUID, command: Create, authorization: Auth, key: Key, expected: Revision) -> Result[Receipt]:
    revision = int(expected)
    signature = fingerprint([str(projectId), revision, command.costHash, sorted(command.evidenceRefs), command.overrunReasons])

    def write(c, a, rid):
        p = _project(c, a, projectId, write=True)
        if not _synthetic(c):
            raise Failure(403, 'POLICY_PENDING', project_id=p.id)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        grant = _grant(c, p, a.person, now)
        if not grant:
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        old = replay(c, a, CREATE, key, signature, Result[Receipt])
        if old:
            if not _grant(c, p, a.person, now, grant_id=old.data.grantId, signed_at=old.data.signedAt):
                raise Failure(403, 'FORBIDDEN', project_id=p.id)
            return old
        if p.stage != 'delivering':
            raise Failure(422, 'RECONCILIATION_LOCKED', project_id=p.id)
        prior = _latest(c, p.id)
        if revision != (prior.revision if prior else 1):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        basis = _basis(c, p, now)
        if command.costHash != basis.digest:
            raise Failure(409, 'RECONCILIATION_STALE', project_id=p.id)
        cutoff, lines = _lines(c, a, p, a.person, command.evidenceRefs, command.overrunReasons, basis, now)
        digest = _receipt_hash(p.id, revision + 1, a.person, grant, now, cutoff, command.costHash, lines, rid)
        row = c.execute(receipts.insert().values(project_id=p.id, revision=revision + 1, signed_by=a.person,
            grant_id=grant, signed_at=now, covered_through=cutoff, rule_version=RULE_VERSION,
            cost_hash=command.costHash, receipt_hash=digest, basis=basis.payload, lines=lines,
            request_id=UUID(rid)).returning(receipts)).mappings().one()
        result = Result[Receipt](data=_view(row), meta=meta(CREATE, _refs(row)))
        body = result.model_dump(mode='json')
        record_receipt(c, a, key, CREATE, rid, row.id, p.id, signature, body, fingerprint(body),
                       prior.receipt_hash if prior else None)
        c.execute(db.outbox_events.insert().values(event_type='cost.reconciled', dedupe_key=rid, status='pending',
            payload={'projectId': str(p.id), 'receiptId': str(row.id), 'receiptHash': digest}))
        return result
    return run(authorization, CREATE, write)
