"""Project health API. Live observations never mutate state or fill in history."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AwareDatetime, Field, StringConstraints, ValidationError, field_serializer

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .pagination import pg_offset
from .contracts import RULE_VERSION, Result, Strict
from .costs import DisplayedRate, Kind, cost_basis
from .dto import Proportion, Unavailable
from .health_calculation import ACTIVE, observe
from .health_schema import appeals, appeal_decisions, zero_budgets
from .health_trace import HealthTrace
from .identity import Failure, explicit_project_scope, visible
from .materials import finish, replay
from .projects import fingerprint, meta, run
from .requirement_baselines import active_member
from .rule_scan import latest, scan_locked

router = APIRouter(prefix='/api/v1', tags=['Project health'])
GET, HISTORY = 'pmo_health_get', 'human_health_history'
REFRESH, APPEAL = 'human_health_refresh', 'human_appeal_health'
REVIEW_APPEAL, DECLARE_ZERO = 'human_review_health_appeal', 'human_health_zero_budget'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
s = db.health_snapshots
p = db.projects
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000, pattern=r'^[^\x00]+$')]


class Explain(Strict):
    reason: Reason


class Review(Strict):
    verdict: Literal['upheld', 'recompute']
    rationale: Reason


class Zero(Explain):
    type: Kind
    baselineHash: str = Field(pattern=r'^[a-f0-9]{64}$')


class ZeroReceipt(Zero):
    declarationId: UUID
    projectId: UUID
    declaredBy: UUID
    declaredAt: datetime


class Appeal(Strict):
    appealId: UUID
    snapshotId: UUID
    appealedBy: UUID
    appealedAt: datetime
    reason: str
    revision: int
    reviewerId: UUID | None = None
    verdict: str | None = None
    rationale: str | None = None
    reviewedAt: datetime | None = None
    canReview: bool = False


class Health(Strict):
    projectId: UUID
    health: Literal['untrusted', 'unavailable', 'green', 'yellow', 'red']
    dataTrust: Literal['trusted', 'untrusted', 'unavailable']
    publication: Literal['official', 'preview', 'withheld', 'unavailable']
    timeLine: Literal['green', 'yellow', 'red'] | None = None
    costLine: Literal['green', 'yellow', 'red'] | None = None
    qualityLine: Literal['green', 'yellow', 'red'] | None = None
    moneyVarianceRate: DisplayedRate | Unavailable | None = None
    effortVarianceRate: DisplayedRate | Unavailable | None = None
    requirementSatisfactionRate: Proportion | Unavailable | None = None
    asOf: datetime
    ruleVersion: str = RULE_VERSION
    sourceRefs: list[str] = Field(max_length=100)
    historical: bool = False
    snapshotId: UUID | None = None
    # The contracted summary above is shared with MCP. Detailed traces remain in the human workspace.
    calculatedAt: datetime | None = None
    inputHash: str | None = None
    eventIds: list[UUID] | None = None
    gaps: list[str] | None = None
    inputs: HealthTrace | None = None
    appeal: Appeal | None = None
    projectRevision: int | None = None
    canAppeal: bool | None = None
    canRefresh: bool | None = None
    zeroBudgetHashes: dict[str, str] | None = None

    @field_serializer('inputs', when_used='unless-none')
    def _serialize_trace(self, value: HealthTrace):
        # Top-level exclusions must not remove observed nulls or fill absent historical fields.
        return value.model_dump(mode='json', exclude_unset=True)


class History(Strict):
    items: list[Health]
    page: int
    pageSize: int
    total: int


def _project(c, a, *, pid=None, snapshot_id=None, appeal_id=None, write=False):
    q = sa.select(p).where(visible(a))
    if a.kind == 'agent':
        q = q.where(explicit_project_scope(a))
    if pid is not None:
        q = q.where(p.c.id == pid)
    else:
        q = q.join(s, s.c.project_id == p.c.id)
        if snapshot_id is not None:
            q = q.where(s.c.id == snapshot_id)
        else:
            q = q.join(appeals, appeals.c.snapshot_id == s.c.id).where(appeals.c.id == appeal_id)
    row = c.execute(q.with_for_update(of=p, read=not write)).mappings().one_or_none()
    if row is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return row


def _role(c, a, project, role):
    return (a.kind == 'human' and project.stage != 'archived' and (role != 'pm' or project.pm_id == a.person)
            and active_member(c, project, a.person, role))


def _appeals_for(c, snapshot_ids, person, reviewer):
    if not snapshot_ids:
        return {}
    ac, dc = appeals.c, appeal_decisions.c
    rows = c.execute(sa.select(appeals, appeal_decisions).select_from(
        appeals.outerjoin(appeal_decisions, dc.appeal_id == ac.id))
        .where(ac.snapshot_id.in_(snapshot_ids), ac.appealed_at.is_not(None))).mappings()
    return {r.snapshot_id: Appeal(appealId=r.id, snapshotId=r.snapshot_id, appealedBy=r.appealed_by,
        appealedAt=r.appealed_at, reason=r.reason, revision=2 if r.reviewer_id else 1, reviewerId=r.reviewer_id,
        verdict=r.verdict, rationale=r.rationale, reviewedAt=r.reviewed_at,
        canReview=reviewer and person != r.appealed_by and r.reviewer_id is None) for r in rows}


def _appeal_allowed(row):
    return row.results.get('publication') in ('official', 'withheld')


def _dto(project, at, values, refs, *, row=None, inputs=None, historical=False, human=False, pm=False, appeal=None):
    compact = dict(values)
    gaps = compact.pop('gaps', None)
    sources = list(dict.fromkeys(([f'health-snapshot:{row.id}'] if row else []) + refs))[:100]
    data = Health(projectId=project.id, asOf=at, ruleVersion=row.rule_version if row else RULE_VERSION,
                  sourceRefs=sources, historical=historical, snapshotId=row.id if row else None, **compact)
    if human:
        data.calculatedAt = row.calculated_at if row else at
        data.inputHash = row.input_hash if row else None
        data.eventIds = [UUID(value) for value in (row.rule_event_ids or [])] if row else None
        data.gaps = gaps
        if inputs is not None:
            try:
                data.inputs = HealthTrace.model_validate(inputs, extra='ignore')
            except ValidationError as exc:
                raise Failure(500, 'HEALTH_TRACE_INVALID', project_id=project.id) from exc
        data.appeal = appeal
        if historical:
            prefix = f'project:{project.id}@'
            saved_revision = next((ref[len(prefix):] for ref in refs if ref.startswith(prefix)), '')
            # Old observations without a stored project source cannot claim the live revision.
            data.projectRevision = int(saved_revision) if saved_revision.isascii() and saved_revision.isdecimal() else None
        else:
            data.projectRevision = project.revision
        data.canRefresh = pm and project.stage in ACTIVE and not historical
        data.canAppeal = pm and row is not None and _appeal_allowed(row) and appeal is None
        if data.canRefresh and data.inputs is not None:
            cost = data.inputs.cost
            data.zeroBudgetHashes = {kind: fingerprint(line.budgetRefs) for kind, line in (('money', cost.money), ('effort', cost.effort))
                if f'{kind.upper()}_ZERO_BUDGET_UNDECLARED' in (gaps or [])}
    return data


def _stored(project, row, *, historical, human, pm=False, appeal=None):
    return _dto(project, row.as_of, row.results, row.source_refs, row=row, inputs=row.input_revisions,
                historical=historical, human=human, pm=pm, appeal=appeal)


def _response(op, item):
    info = meta(op, item.sourceRefs, project_id=item.projectId)
    info.asOf, info.ruleVersion = item.asOf, item.ruleVersion
    return Result[Health](data=item, meta=info)


def current(c, a, project, now, *, human: bool, pm: bool = False) -> Health:
    if project.stage not in ACTIVE:
        return _dto(project, now, {'health': 'unavailable', 'dataTrust': 'unavailable',
            'publication': 'unavailable', 'gaps': ['HEALTH_NOT_ACTIVE']},
            [f'project:{project.id}@{project.revision}'], human=human)
    row = latest(c, project.id, now)
    obs = observe(c, project, now)
    if row and row.input_hash == obs.digest:
        appeal = _appeals_for(c, [row.id], a.person, _role(c, a, project, 'reviewer')).get(row.id) if human else None
        return _stored(project, row, historical=False, human=human, pm=pm, appeal=appeal)
    values = dict(obs.results)
    if values['publication'] == 'official':
        values['publication'] = 'preview'  # Scanning or a human refresh publishes a durable observation.
    return _dto(project, now, values, obs.refs, inputs=obs.inputs, human=human, pm=pm)


@router.get('/projects/{projectId}/health', operation_id=GET, response_model_exclude_none=True)
def get(projectId: UUID, authorization: Auth, asOf: AwareDatetime | None = None) -> Result[Health]:
    def read(c, a, _):
        project = _project(c, a, pid=projectId)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        if asOf is not None and asOf > now:
            raise Failure(422, 'FUTURE_AS_OF', project_id=projectId)
        pm = _role(c, a, project, 'pm')
        human = a.kind == 'human'
        if asOf is not None:
            row = latest(c, projectId, asOf)
            if not row:
                raise Failure(404, 'HEALTH_SNAPSHOT_NOT_FOUND', project_id=projectId)
            return _response(GET, _stored(project, row, historical=True, human=human))
        return _response(GET, current(c, a, project, now, human=human, pm=pm))
    return run(authorization, GET, read)


@router.get('/projects/{projectId}/health/history', operation_id=HISTORY, response_model_exclude_none=True,
            openapi_extra=HUMAN)
def history(projectId: UUID, authorization: Auth, page: int = Query(1, ge=1),
            pageSize: int = Query(10, ge=1, le=50)) -> Result[History]:
    def read(c, a, _):
        project = _project(c, a, pid=projectId)
        conditions = (s.c.project_id == project.id, s.c.results.has_key('publication'))
        total = c.scalar(sa.select(sa.func.count()).select_from(s).where(*conditions))
        rows = list(c.execute(sa.select(s).where(*conditions).order_by(s.c.as_of.desc(), s.c.id.desc())
            .offset(pg_offset(page,pageSize)).limit(pageSize)).mappings())
        receipts = _appeals_for(c, [row.id for row in rows], a.person, _role(c, a, project, 'reviewer'))
        pm = _role(c, a, project, 'pm')
        items = [_stored(project, row, historical=True, human=True, pm=pm,
                         appeal=receipts.get(row.id)) for row in rows]
        return Result[History](data=History(items=items, page=page, pageSize=pageSize, total=total),
                               meta=meta(HISTORY, [f'health-snapshot:{row.id}' for row in rows]))
    return run(authorization, HISTORY, read)


@router.post('/projects/{projectId}/health/refresh', operation_id=REFRESH, response_model_exclude_none=True,
             openapi_extra=HUMAN)
def refresh(projectId: UUID, authorization: Auth, key: Key, expected: Revision) -> Result[Health]:
    signature = fingerprint([str(projectId), int(expected)])

    def write(c, a, rid):
        project = _project(c, a, pid=projectId, write=True)
        if not _role(c, a, project, 'pm'):
            raise Failure(403, 'FORBIDDEN', project_id=projectId)
        if prior := replay(c, a, REFRESH, key, signature, Result[Health]):
            return prior
        if project.revision != int(expected):
            raise Failure(409, 'VERSION_CONFLICT', project_id=projectId)
        if project.stage not in ACTIVE:
            raise Failure(422, 'HEALTH_NOT_ACTIVE', project_id=projectId)
        row = scan_locked(c, project, c.scalar(sa.select(sa.func.clock_timestamp())))
        current_appeal = _appeals_for(c, [row.id], a.person, False).get(row.id)
        result = _response(REFRESH, _stored(project, row, historical=False, human=True, pm=True, appeal=current_appeal))
        return finish(c, a, REFRESH, key, rid, projectId, signature, result)
    return run(authorization, REFRESH, write)


@router.post('/projects/{projectId}/health/zero-budget', operation_id=DECLARE_ZERO, response_model_exclude_none=True,
             openapi_extra=HUMAN)
def declare_zero(projectId: UUID, command: Zero, authorization: Auth, key: Key, expected: Revision) -> Result[ZeroReceipt]:
    signature = fingerprint([str(projectId), int(expected), command.model_dump()])

    def write(c, a, rid):
        project = _project(c, a, pid=projectId, write=True)
        if not _role(c, a, project, 'pm'):
            raise Failure(403, 'FORBIDDEN', project_id=projectId)
        if prior := replay(c, a, DECLARE_ZERO, key, signature, Result[ZeroReceipt]):
            return prior
        if project.revision != int(expected):
            raise Failure(409, 'VERSION_CONFLICT', project_id=projectId)
        if project.stage not in ACTIVE:
            raise Failure(422, 'HEALTH_NOT_ACTIVE', project_id=projectId)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        basis = cost_basis(c, projectId, now)
        refs = basis.budget_refs(command.type)
        digest = fingerprint(refs)
        if digest != command.baselineHash:
            raise Failure(409, 'ZERO_BUDGET_BASIS_STALE', project_id=projectId)
        if basis.amounts(command.type) != (0, 0, 0) or not refs:
            raise Failure(422, 'EXPLICIT_ZERO_BUDGET_REQUIRED', project_id=projectId)
        z = zero_budgets.c
        if c.scalar(sa.select(z.id).where(z.project_id == projectId, z.kind == command.type, z.baseline_hash == digest)):
            raise Failure(409, 'ZERO_BUDGET_ALREADY_DECLARED', project_id=projectId)
        row = c.execute(zero_budgets.insert().values(project_id=projectId, kind=command.type, baseline_hash=digest,
            reason=command.reason, declared_by=a.person, declared_at=now).returning(zero_budgets)).mappings().one()
        result = Result[ZeroReceipt](data=ZeroReceipt(**command.model_dump(), declarationId=row.id, projectId=projectId,
            declaredBy=row.declared_by, declaredAt=row.declared_at), meta=meta(DECLARE_ZERO, [f'zero-budget:{row.id}', *refs]))
        return finish(c, a, DECLARE_ZERO, key, rid, projectId, signature, result)
    return run(authorization, DECLARE_ZERO, write)


@router.post('/health-snapshots/{snapshotId}/appeals', operation_id=APPEAL, response_model_exclude_none=True,
             openapi_extra={**HUMAN, 'x-requiredRole': 'pm'})
def appeal_health(snapshotId: UUID, command: Explain, authorization: Auth, key: Key, expected: Revision) -> Result[Appeal]:
    signature = fingerprint([str(snapshotId), int(expected), command.model_dump()])

    def write(c, a, rid):
        project = _project(c, a, snapshot_id=snapshotId, write=True)
        if not _role(c, a, project, 'pm'):
            raise Failure(403, 'FORBIDDEN', project_id=project.id)
        if prior := replay(c, a, APPEAL, key, signature, Result[Appeal]):
            return prior
        if int(expected) != 1:
            raise Failure(409, 'VERSION_CONFLICT', project_id=project.id)
        row = c.execute(sa.select(s).where(s.c.id == snapshotId, s.c.project_id == project.id)).mappings().one()
        if not _appeal_allowed(row):
            raise Failure(422, 'HEALTH_OBSERVATION_NOT_APPEALABLE', project_id=project.id)
        if c.scalar(sa.select(appeals.c.id).where(appeals.c.snapshot_id == snapshotId).limit(1)):
            raise Failure(409, 'HEALTH_APPEAL_LIMIT', project_id=project.id)
        saved = c.execute(appeals.insert().values(snapshot_id=snapshotId, appealed_by=a.person, reason=command.reason,
            appealed_at=sa.func.clock_timestamp()).returning(appeals)).mappings().one()
        result = Result[Appeal](data=Appeal(appealId=saved.id, snapshotId=snapshotId, appealedBy=a.person,
            appealedAt=saved.appealed_at, reason=saved.reason, revision=1),
            meta=meta(APPEAL, [f'health-snapshot:{snapshotId}', f'health-appeal:{saved.id}@1']))
        return finish(c, a, APPEAL, key, rid, project.id, signature, result, row.input_hash)
    return run(authorization, APPEAL, write)


@router.post('/health-appeals/{appealId}/review', operation_id=REVIEW_APPEAL, response_model_exclude_none=True,
             openapi_extra=HUMAN)
def review_appeal(appealId: UUID, command: Review, authorization: Auth, key: Key, expected: Revision) -> Result[Appeal]:
    signature = fingerprint([str(appealId), int(expected), command.model_dump()])

    def write(c, a, rid):
        project = _project(c, a, appeal_id=appealId, write=True)
        row = c.execute(sa.select(appeals).where(appeals.c.id == appealId)).mappings().one()
        if not _role(c, a, project, 'reviewer') or row.appealed_by == a.person:
            raise Failure(403, 'INDEPENDENT_REVIEWER_REQUIRED', project_id=project.id)
        if prior := replay(c, a, REVIEW_APPEAL, key, signature, Result[Appeal]):
            return prior
        if int(expected) != 1 or c.scalar(sa.select(appeal_decisions.c.appeal_id).where(appeal_decisions.c.appeal_id == appealId)):
            raise Failure(409, 'VERSION_CONFLICT', project_id=project.id)
        if row.appealed_at is None:
            raise Failure(422, 'HEALTH_APPEAL_UNVERIFIED', project_id=project.id)
        c.execute(appeal_decisions.insert().values(appeal_id=appealId, reviewer_id=a.person, verdict=command.verdict,
                                                rationale=command.rationale, request_id=UUID(rid)))
        value = _appeals_for(c, [row.snapshot_id], a.person, False)[row.snapshot_id]
        result = Result[Appeal](data=value,
            meta=meta(REVIEW_APPEAL, [f'health-snapshot:{row.snapshot_id}', f'health-appeal:{appealId}@2']))
        return finish(c, a, REVIEW_APPEAL, key, rid, project.id, signature, result)
    return run(authorization, REVIEW_APPEAL, write)
