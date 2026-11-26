"""The human and delegated Agent entrances share immutable source checks."""
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AfterValidator, Field, StrictBool, StrictStr

from . import fact_store as store
from .access import Auth, Key, Revision
from .pagination import pg_offset
from .contracts import Result, Strict
from .fact_sources import (Candidate, Short, Status, Trust, Value, can_propose, concrete, current_effort_source,
                           fact_labels, high_evidence, normal_value, recheck, submitted, verified_source)
from .identity import Failure
from .materials import finish, replay
from .projects import fingerprint, meta, run
from .report_analysis import permitted as can_read_analysis
from .reports import TZ, _context, _plain, _ref, current_publication_fields, readable_reports
from .requirement_baselines import active_member

router = APIRouter(prefix='/api/v1', tags=['Fact evidence'])
LIST, PROPOSE = 'human_report_fact_list', 'pmo_fact_propose'
VERIFY, CLARIFY = 'human_fact_verify', 'human_fact_clarify'
HISTORY = 'human_fact_history'
COMPARE = 'human_fact_weekly_comparison'
COMPARE_REPORT = 'human_report_facts_weekly_comparison'
REQUEST_CLARIFICATION = 'human_fact_weekly_clarification_request'
COMPARISON_SCAN_LIMIT = 250
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
Reason = Annotated[StrictStr, Field(min_length=5, max_length=2000, pattern=r'\S'), AfterValidator(_plain)]


class Verify(Strict):
    trust: Trust
    evidenceRef: Short
    reason: Reason | None = None


class Clarify(Strict):
    reason: Reason
    correctedSourceRef: Short


class Fact(Strict):
    factId: UUID
    reportId: UUID
    projectId: UUID
    subject: str
    predicate: str
    trust: Trust
    validationStatus: Status
    sourceRef: str
    quote: str
    typedValue: Value
    asOf: datetime
    revision: int
    quotePath: str | None = None
    quoteOffset: int | None = None
    contentHash: str | None = None
    verificationEvidenceRef: str | None = None
    verificationNote: str | None = None


class Page(Strict):
    items: list[Fact] = Field(max_length=50)
    page: int
    pageSize: int
    total: int
    reportRevision: int
    canReadAnalysis: bool
    canPropose: bool
    canVerify: bool
    canClarify: bool


class RevisionEntry(Strict):
    revision: int
    action: str
    actorId: UUID
    occurredAt: datetime
    validationStatus: Status
    trust: Trust
    reason: str
    evidenceRef: str | None = None


class HistoryPage(Strict):
    items: list[RevisionEntry] = Field(max_length=50)
    total: int
    page: int
    pageSize: int


class ComparisonValue(Strict):
    asOf: datetime
    typedValue: Value
    quote: str
    factRef: str
    reportRef: str
    evidenceRef: str | None = None


class WeeklyComparison(Strict):
    status: Literal['same', 'changed', 'current_unverified', 'no_previous_fact', 'scan_limited', 'outside_reporting_week']
    previousStart: date
    previousEnd: date
    previous: ComparisonValue | None = None


class ReportComparisonEntry(Strict):
    fact: Fact
    comparison: WeeklyComparison
    canRequestClarification: bool


class ReportComparisonPage(Strict):
    items: list[ReportComparisonEntry]
    page: int
    pageSize: int
    total: int


class WeeklyClarification(Strict):
    previousFactRef: Annotated[Short, Field(pattern=r'^fact:[0-9a-f-]{36}@[1-9][0-9]{0,18}\z')]
    reason: Reason
    confirmed: StrictBool


def view(row, *, human: bool) -> Fact:
    extra = dict(quotePath=row.quote_path, quoteOffset=row.quote_offset, contentHash=row.content_hash,
        verificationEvidenceRef=row.evidence_ref, verificationNote=row.history_reason) if human else {}
    return Fact(factId=row.id, reportId=row.report_id, projectId=row.project_id, subject=row.subject,
        predicate=row.predicate, trust=row.trust, validationStatus=row.validation_status, sourceRef=row.source_ref,
        quote=row.quote, typedValue=row.value, asOf=row.as_of, revision=row.revision, **extra)


def _result(row, op, human):
    refs = list(dict.fromkeys([row.source_ref, store.fact_ref(row), *([row.evidence_ref] if row.evidence_ref else [])]))
    return Result[Fact](data=view(row, human=human), meta=meta(op, refs))


def _fact_context(c, who, fid, *, write=True):
    f = store.f
    report_id = c.scalar(sa.select(f.report_id).join(store.sources, store.s.fact_id == f.id).where(f.id == fid))
    if report_id is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    p, report, pm = _context(c, who, report_id=report_id, write=write)
    row = c.execute(store.query().where(f.id == fid, f.report_id == report.id, f.project_id == p.id)).mappings().one_or_none()
    if not row:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    return p, report, pm, row


@router.get('/facts/{factId}/history', operation_id=HISTORY, response_model_exclude_none=True, openapi_extra=HUMAN)
def history(factId: UUID, authorization: Auth, page: int = Query(1, ge=1),
            pageSize: int = Query(20, ge=1, le=50)) -> Result[HistoryPage]:
    def read(c, a, _):
        _, _, _, fact = _fact_context(c, a, factId, write=False)
        j = store.j
        q = sa.select(store.journal).where(j.fact_id == factId)
        total = c.scalar(sa.select(sa.func.count()).select_from(q.subquery()))
        rows = c.execute(q.order_by(j.revision.desc()).offset(pg_offset(page,pageSize)).limit(pageSize)).mappings()
        items = [RevisionEntry(revision=row.revision, action=row.action, actorId=row.actor_id, occurredAt=row.occurred_at,
            validationStatus=row.validation_status, trust=row.trust, reason=row.reason, evidenceRef=row.evidence_ref) for row in rows]
        return Result[HistoryPage](data=HistoryPage(items=items, total=total, page=page, pageSize=pageSize),
                                   meta=meta(HISTORY, [fact.source_ref, *(f'fact:{factId}@{item.revision}' for item in items)]))
    return run(authorization, HISTORY, read)


def _comparison_value(row):
    return ComparisonValue(asOf=row.as_of.astimezone(UTC), typedValue=row.value, quote=row.quote, factRef=store.fact_ref(row),
                           reportRef=row.source_ref, evidenceRef=row.evidence_ref if row.trust == 'high' else None)


def _previous_rows(c, actor, project, previous_monday, current_monday, *, excluded_report=None):
    allowed = readable_reports(actor, project.id).subquery()
    f = store.f
    lower, upper = (datetime.combine(day, time.min, TZ) for day in (previous_monday, current_monday))
    q = store.query().join(allowed, allowed.c.id == f.report_id).where(
        f.project_id == project.id, f.as_of >= lower, f.as_of < upper,
        f.validation_status == 'verified', f.trust.in_(('high', 'medium')))
    if excluded_report is not None:
        q = q.where(f.report_id != excluded_report)
    return c.execute(q.order_by(f.as_of.desc(), f.id).limit(COMPARISON_SCAN_LIMIT + 1)).mappings().all()


def _compare(c, report, fact, monday, rows, load_report, *, fixed_week=False) -> WeeklyComparison:
    args = {'previousStart': monday - timedelta(days=7), 'previousEnd': monday - timedelta(days=1)}
    if fixed_week and not monday <= fact.as_of.astimezone(TZ).date() < report.period_end:
        return WeeklyComparison(status='outside_reporting_week', **args)
    if not verified_source(c, report, fact):
        return WeeklyComparison(status='current_unverified', **args)
    labels = fact_labels(fact.subject, fact.predicate)
    for row in rows[:COMPARISON_SCAN_LIMIT]:
        if fact_labels(row.subject, row.predicate) != labels:
            continue
        source = load_report(row.report_id)
        if source is not None and verified_source(c, source, row):
            status: Literal['same', 'changed'] = 'same' if normal_value(fact.value) == normal_value(row.value) else 'changed'
            return WeeklyComparison(status=status, previous=_comparison_value(row), **args)
    return WeeklyComparison(status='scan_limited' if len(rows) > COMPARISON_SCAN_LIMIT else 'no_previous_fact', **args)


def _report_lookup(c, actor, project):
    q = readable_reports(actor, project.id)
    cache = {}

    def lookup(report_id):
        if report_id not in cache:
            cache[report_id] = c.execute(q.where(q.selected_columns.id == report_id)).mappings().one_or_none()
        return cache[report_id]
    return lookup


def _comparison_refs(comparison: WeeklyComparison) -> list[str]:
    prior = comparison.previous
    return [prior.factRef, prior.reportRef, *([prior.evidenceRef] if prior.evidenceRef else [])] if prior else []


@router.get('/facts/{factId}/weekly-comparison', operation_id=COMPARE,
            response_model_exclude_none=True, openapi_extra=HUMAN)
def weekly_comparison(factId: UUID, authorization: Auth) -> Result[WeeklyComparison]:
    def read(c, actor, _):
        project, report, _pm, fact = _fact_context(c, actor, factId, write=False)
        local_date = fact.as_of.astimezone(TZ).date()
        current_monday = local_date - timedelta(days=local_date.weekday())
        rows = _previous_rows(c, actor, project, current_monday - timedelta(days=7), current_monday)
        data = _compare(c, report, fact, current_monday, rows, _report_lookup(c, actor, project))
        return Result[WeeklyComparison](data=data,
            meta=meta(COMPARE, [fact.source_ref, store.fact_ref(fact), *_comparison_refs(data)], project_id=project.id))
    return run(authorization, COMPARE, read)


def _comparison_report(c, report):
    now = c.scalar(sa.select(sa.func.clock_timestamp()))
    if report.type != 'weekly' or current_publication_fields(report, now) is None:
        raise Failure(422, 'WEEKLY_FACT_REQUIRES_CURRENT_PUBLICATION', project_id=report.project_id)


def _can_request(c, project, fact):
    # This path is a human challenge to one observation. Existing same-time contradictions
    # remain under their original verification and canonical resolution workflow.
    return (bool(fact.evidence_ref) and store.open_case(c, fact.group_key) is None
            and len(store.members(c, project, fact.group_key)) == 1)


@router.get('/reports/{reportId}/facts/weekly-comparison', operation_id=COMPARE_REPORT,
            response_model_exclude_none=True, openapi_extra=HUMAN)
def report_weekly_comparison(reportId: UUID, authorization: Auth, page: int = Query(1, ge=1),
                             pageSize: int = Query(10, ge=1, le=20)) -> Result[ReportComparisonPage]:
    def read(c, actor, _):
        project, report, pm = _context(c, actor, report_id=reportId)
        _comparison_report(c, report)
        f = store.f
        q = store.query().where(f.project_id == project.id, f.report_id == report.id)
        total = c.scalar(sa.select(sa.func.count()).select_from(q.subquery()))
        facts = c.execute(q.order_by(f.as_of.desc(), f.id).offset(pg_offset(page, pageSize)).limit(pageSize)).mappings().all()
        monday = report.period_start
        rows = _previous_rows(c, actor, project, monday - timedelta(days=7), monday, excluded_report=report.id) if facts else []
        lookup = _report_lookup(c, actor, project)
        items = []
        refs = [_ref(report)]
        for fact in facts:
            current = _compare(c, report, fact, monday, rows, lookup, fixed_week=True)
            items.append(ReportComparisonEntry(fact=view(fact, human=True), comparison=current,
                canRequestClarification=bool(pm and project.stage != 'archived' and current.status == 'changed'
                                             and _can_request(c, project, fact))))
            refs.extend([store.fact_ref(fact), *_comparison_refs(current)])
        return Result[ReportComparisonPage](data=ReportComparisonPage(items=items, page=page, pageSize=pageSize,
            total=total), meta=meta(COMPARE_REPORT, list(dict.fromkeys(refs)), project_id=project.id))
    return run(authorization, COMPARE_REPORT, read)


@router.post('/facts/{factId}/weekly-clarification', operation_id=REQUEST_CLARIFICATION,
             response_model_exclude_none=True, openapi_extra=HUMAN)
def request_weekly_clarification(factId: UUID, command: WeeklyClarification, authorization: Auth,
                                 key: Key, expected: Revision) -> Result[Fact]:
    signature = fingerprint([str(factId), int(expected), command.model_dump()])

    def write(c, actor, rid):
        project, report, pm, fact = _fact_context(c, actor, factId)
        if not pm:
            raise Failure(403, 'FORBIDDEN', project_id=project.id)
        if saved := replay(c, actor, REQUEST_CLARIFICATION, key, signature, Result[Fact]):
            return saved
        _editable(project, report, fact, expected)
        _comparison_report(c, report)
        if not command.confirmed or len(command.reason.strip()) < 5:
            raise Failure(422, 'WEEKLY_FACT_CONFIRMATION_REQUIRED', project_id=project.id)
        monday = report.period_start
        rows = _previous_rows(c, actor, project, monday - timedelta(days=7), monday, excluded_report=report.id)
        result = _compare(c, report, fact, monday, rows, _report_lookup(c, actor, project), fixed_week=True)
        if result.status != 'changed' or result.previous is None or result.previous.factRef != command.previousFactRef:
            raise Failure(409, 'WEEKLY_FACT_COMPARISON_CHANGED', project_id=project.id)
        if not _can_request(c, project, fact):
            raise Failure(422, 'WEEKLY_FACT_CLARIFICATION_UNAVAILABLE', project_id=project.id)
        reason = f'{command.reason.strip()} (对照 {result.previous.factRef})'
        updated = store.revise(c, fact, actor.person, rid, status='disputed', trust=fact.trust,
            action='disputed', reason=reason, evidence_ref=fact.evidence_ref, evidence_hash=fact.evidence_hash)
        store.escalate(c, project, updated, actor.person, reason)
        out = _result(updated, REQUEST_CLARIFICATION, True)
        out.meta.sourceRefs = list(dict.fromkeys([*out.meta.sourceRefs, *_comparison_refs(result)]))
        return finish(c, actor, REQUEST_CLARIFICATION, key, rid, project.id, signature, out, fingerprint(store.fact_ref(fact)))
    return run(authorization, REQUEST_CLARIFICATION, write)


@router.get('/reports/{reportId}/facts', operation_id=LIST, response_model_exclude_none=True, openapi_extra=HUMAN)
def listing(reportId: UUID, authorization: Auth, page: int = Query(1, ge=1),
            pageSize: int = Query(15, ge=1, le=50)) -> Result[Page]:
    def read(c, a, _):
        p, report, pm = _context(c, a, report_id=reportId)
        f = store.f
        q = store.query().where(f.project_id == p.id, f.report_id == report.id)
        total = c.scalar(sa.select(sa.func.count()).select_from(q.subquery()))
        rows = list(c.execute(q.order_by(f.created_at.desc(), f.id).offset(pg_offset(page,pageSize)).limit(pageSize)).mappings())
        writable = p.stage != 'archived' and submitted(report)
        return Result[Page](data=Page(items=[view(row, human=True) for row in rows], page=page, pageSize=pageSize,
            total=total, reportRevision=report.revision, canReadAnalysis=can_read_analysis(c, p, report, a.person),
            canPropose=writable and can_propose(c, a, p, report),
            canVerify=writable and (pm or active_member(c, p, a.person, 'reviewer')),
            canClarify=writable and pm), meta=meta(LIST, [_ref(report), *(store.fact_ref(row) for row in rows)]))
    return run(authorization, LIST, read)


@router.post('/reports/{reportId}/facts/proposals', operation_id=PROPOSE, response_model_exclude_none=True)
def propose(reportId: UUID, command: Candidate, authorization: Auth, key: Key, expected: Revision) -> Result[Fact]:
    signature = fingerprint([str(reportId), int(expected), command.model_dump(mode='json', exclude_none=True)])

    def write(c, a, rid):
        p, report, _ = _context(c, a, report_id=reportId, write=True)
        if not can_propose(c, a, p, report):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        if saved := replay(c, a, PROPOSE, key, signature, Result[Fact]):
            return saved
        if report.revision != int(expected):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        row = store.propose_locked(c, p, report, command, a.person, rid)
        return finish(c, a, PROPOSE, key, rid, p.id, signature, _result(row, PROPOSE, a.kind == 'human'))
    return run(authorization, PROPOSE, write)


@router.post('/facts/{factId}/verify', operation_id=VERIFY, response_model_exclude_none=True, openapi_extra=HUMAN)
def verify(factId: UUID, command: Verify, authorization: Auth, key: Key, expected: Revision) -> Result[Fact]:
    signature = fingerprint([str(factId), int(expected), command.model_dump()])

    def write(c, a, rid):
        p, report, pm, fact = _fact_context(c, a, factId)
        if not pm and not active_member(c, p, a.person, 'reviewer'):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        if saved := replay(c, a, VERIFY, key, signature, Result[Fact]):
            return saved
        _editable(p, report, fact, expected)
        ref, digest = command.evidenceRef, None
        if command.trust == 'high':
            if not command.reason:
                raise Failure(422, 'FACT_EVIDENCE_LOCATOR_REQUIRED', project_id=p.id)
            ref, digest = high_evidence(c, p.id, report.id, ref)
        elif ref != fact.source_ref:
            raise Failure(422, 'FACT_SOURCE_MISMATCH', project_id=p.id)
        elif command.trust == 'medium' and not (p.pm_id == report.submitted_by
                and active_member(c, p, report.submitted_by, 'pm') and concrete(fact.value, fact.quote)):
            raise Failure(422, 'FACT_PM_SPECIFIC_SOURCE_REQUIRED', project_id=p.id)
        disputed = command.trust == 'low' or store.open_case(c, fact.group_key) is not None
        updated = store.revise(c, fact, a.person, rid, status='disputed' if disputed else 'verified', trust=command.trust,
            action='verified', reason=command.reason or ('FACT_LOW_TRUST' if command.trust == 'low' else 'SOURCE_VERIFIED'),
            evidence_ref=ref, evidence_hash=digest)
        if command.trust == 'low':
            store.escalate(c, p, updated, a.person, 'FACT_LOW_TRUST')
        return finish(c, a, VERIFY, key, rid, p.id, signature, _result(updated, VERIFY, True), fingerprint(store.fact_ref(fact)))
    return run(authorization, VERIFY, write)


def _editable(project, report, fact, expected):
    if project.stage == 'archived':
        raise Failure(422, 'ARCHIVED_READ_ONLY', project_id=project.id)
    if fact.revision != int(expected):
        raise Failure(409, 'VERSION_CONFLICT', project_id=project.id)
    if fact.validation_status == 'rejected':
        raise Failure(422, 'FACT_SUPERSEDED', project_id=project.id)
    if fact.quote_path.startswith('/structuredFields/effort_breakdown/') and not current_effort_source(report):
        raise Failure(409, 'FACT_SOURCE_STALE', project_id=project.id)
    recheck(report, fact)


@router.post('/facts/{factId}/clarify', operation_id=CLARIFY, response_model_exclude_none=True, openapi_extra=HUMAN)
def clarify(factId: UUID, command: Clarify, authorization: Auth, key: Key, expected: Revision) -> Result[Fact]:
    signature = fingerprint([str(factId), int(expected), command.model_dump()])

    def write(c, a, rid):
        p, report, pm, fact = _fact_context(c, a, factId)
        if not pm:
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        if saved := replay(c, a, CLARIFY, key, signature, Result[Fact]):
            return saved
        _editable(p, report, fact, expected)
        case = store.open_case(c, fact.group_key)
        if not case or fact.trust == 'low' or not fact.evidence_ref:
            raise Failure(422, 'FACT_CLARIFICATION_REQUIRES_VERIFIED_SOURCE', project_id=p.id)
        if command.correctedSourceRef != fact.evidence_ref:
            raise Failure(422, 'FACT_SOURCE_MISMATCH', project_id=p.id)
        if fact.trust == 'high':
            _, digest = high_evidence(c, p.id, report.id, fact.evidence_ref)
            if digest != fact.evidence_hash:
                raise Failure(409, 'FACT_SOURCE_STALE', project_id=p.id)
        for other in store.members(c, p, fact.group_key):
            if other.id != factId:
                store.revise(c, other, a.person, rid, status='rejected', trust='low', action='superseded',
                    reason=command.reason, evidence_ref=store.fact_ref(fact))
        updated = store.revise(c, fact, a.person, rid, status='verified', trust=fact.trust, action='resolved',
            reason=command.reason, evidence_ref=fact.evidence_ref, evidence_hash=fact.evidence_hash)
        store.close_case(c, p, case, a.person, fact.evidence_ref)
        return finish(c, a, CLARIFY, key, rid, p.id, signature, _result(updated, CLARIFY, True), fingerprint(store.fact_ref(fact)))
    return run(authorization, CLARIFY, write)
