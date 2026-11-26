from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import re
from typing import Annotated, Literal
from unicodedata import normalize
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AwareDatetime, Field, model_validator

from . import reports as reports_service
from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .cost_categories import CATEGORY_KIND, Category, Kind
from .evidence import lookup_for_integrity, require_verified_content
from .identity import Failure, visible
from .materials import finish, replay
from .pagination import pg_offset
from .projects import fingerprint, meta, run
from .risks import can_manage

router = APIRouter(prefix='/api/v1',tags=['Cost postings'])
t = db.cost_postings
SIGNED = r'^-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$'
OPEN = ('drafting','charter_review','designing','feasibility_review','executing','on_hold','delivering','closing_review')


PostingFilter = Category | Literal['uncategorized']
UNCATEGORIZED = 'posting_ledger'
CATEGORIZED_PROPOSE = 'human_categorized_cost_posting_propose'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
WEEKLY_EFFORT_PREVIEW = 'human_weekly_report_effort_preview'
WEEKLY_EFFORT_PROPOSE = 'human_weekly_report_effort_propose'
WEEKLY_SOURCE_PREFIX = 'weekly-report-effort:'
WEEKLY_SOURCE = re.compile(r'^weekly-report-effort:([0-9a-f-]{36})@([1-9][0-9]{0,18})$')


class Candidate(Strict):
    type: Kind
    amount: Annotated[str,Field(pattern=SIGNED)]
    period: Annotated[str,Field(pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])$')]
    sourceRef: str = Field(min_length=3,max_length=256,pattern=r'\S')


class CategorizedCandidate(Candidate):
    category: Category

    @model_validator(mode='after')
    def check_category(self):
        if CATEGORY_KIND[self.category] != self.type:
            raise ValueError('Category does not apply to the posting unit')
        return self


class Reason(Strict):
    reason: str = Field(min_length=4,max_length=2000,pattern=r'\S')


class Verify(Reason):
    occurredAt: AwareDatetime | None = None
    evidenceRef: Annotated[str, Field(pattern=r'^evidence:[0-9a-fA-F-]{36}$')] | None = None


class WeeklyEffortCandidate(Strict):
    period: Annotated[str, Field(pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])\z')]
    expectedAmount: Annotated[str, Field(pattern=r'^(0|[1-9][0-9]{0,15})\.[0-9]{2}\z')]


class Posting(Strict):
    postingId: UUID
    projectId: UUID
    type: Kind
    status: Literal['draft','pending','verified']
    amountSigned: str
    sourceRef: str


class Detail(Posting):
    period: str
    category: Category | None = None
    revision: int
    occurredAt: datetime | None
    verifiedAt: datetime | None
    postingOfId: UUID | None
    reversalReason: str | None
    verificationNote: str | None
    reportWeekStart: date | None = None
    reportWeekEnd: date | None = None
    canVerify: bool
    canReverse: bool
    classificationId: UUID | None = None
    canClassify: bool | None = None
    pendingClassificationId: UUID | None = None
    pendingCategory: Category | None = None
    pendingClassificationReason: str | None = None


class Page(Strict):
    items: list[Detail]
    page: int
    pageSize: int
    total: int
    canPropose: bool


class WeeklyEffortPreview(Strict):
    reportRef: str
    declaredPersonDays: str
    verifiedPersonDays: str
    candidatePersonDays: str
    allowedPeriods: list[str]
    existing: Posting | None = None
    canPropose: bool


def item(r):
    return Posting(postingId=r.id,projectId=r.project_id,type=r.type,status=r.status,amountSigned=format(r.amount_signed,'.2f'),sourceRef=r.source_ref)


def project(c,a,pid,*,lock=False):
    p=db.projects
    stmt=sa.select(p).where(p.c.id==pid,visible(a))
    row=c.execute(stmt.with_for_update() if lock else stmt).mappings().one_or_none()
    if row is None: raise Failure(404,'RESOURCE_NOT_FOUND')
    return row


def finance(c,a):
    r=db.user_roles
    return a.kind=='human' and bool(c.scalar(sa.select(r.c.id).where(r.c.user_id==a.account,r.c.role=='finance_verifier').with_for_update(read=True)))


def period_start(value):
    try: return date.fromisoformat(value+'-01')
    except ValueError: raise Failure(422,'INVALID_PERIOD') from None


def bucket(c,a,pid,kind,start,category=UNCATEGORIZED):
    e=db.cost_entries
    end=start.replace(day=monthrange(start.year,start.month)[1])
    cond=(e.c.project_id==pid,e.c.type==kind,e.c.period_start==start,e.c.period_end==end,e.c.category==category,e.c.is_current_baseline.is_(False))
    existing=c.scalar(sa.select(e.c.id).where(*cond).limit(1))
    if existing: return existing
    # A collection bucket for preapproval costs is not an approved zero budget.
    return c.scalar(e.insert().values(project_id=pid,type=kind,category=category,period_start=start,period_end=end,
        created_by=a.person,is_current_baseline=False,budget=0,actual=0,forecast_remaining=0,baseline_version=1).returning(e.c.id))


def receipt(c,a,op,key,rid,pid,sig,row,before=None,*,evidence_ref=None):
    out=item(row)
    refs = [f'cost-posting:{row.id}@{2 if row.status=="verified" else 1}']
    if _bound_weekly_posting(row):
        refs.append(f'report:{row.source_ref.removeprefix(WEEKLY_SOURCE_PREFIX)}')
    if evidence_ref:
        refs.append(evidence_ref)
    return finish(c,a,op,key,rid,pid,sig,Result[Posting](data=out,meta=meta(op,refs)),before)


@router.post('/projects/{projectId}/cost-postings',operation_id='pmo_cost_posting_propose')
def propose(projectId:UUID,command:Candidate,authorization:Auth,key:Key)->Result[Posting]:
    return _propose(projectId, command, authorization, key, 'pmo_cost_posting_propose', UNCATEGORIZED)


@router.post('/projects/{projectId}/cost-postings/categorized', operation_id=CATEGORIZED_PROPOSE, openapi_extra=HUMAN)
def propose_categorized(projectId: UUID, command: CategorizedCandidate, authorization: Auth, key: Key) -> Result[Posting]:
    return _propose(projectId, command, authorization, key, CATEGORIZED_PROPOSE, command.category)


def _propose(projectId, command, authorization, key, op, category):
    sig=fingerprint([str(projectId),command.model_dump()])
    def write(c,a,rid):
        p=project(c,a,projectId,lock=True)
        if not can_manage(c,a,p): raise Failure(403,'FORBIDDEN')
        old=replay(c,a,op,key,sig,Result[Posting])
        if old: return old
        if p.stage not in OPEN: raise Failure(422,'LEDGER_LOCKED')
        start=period_start(command.period); amount=Decimal(command.amount); ref=command.sourceRef.strip()
        if amount<=0: raise Failure(422,'POSITIVE_POSTING_REQUIRED')
        if len(ref)<3: raise Failure(422,'SOURCE_REQUIRED')
        if start>c.scalar(sa.select(sa.func.clock_timestamp())).astimezone(UTC).date(): raise Failure(422,'FUTURE_PERIOD')
        normalized_ref=normalize('NFKC',' '.join(ref.split())).casefold()
        if normalized_ref.startswith(WEEKLY_SOURCE_PREFIX):
            raise Failure(422,'WEEKLY_EFFORT_DEDICATED_ROUTE')
        source=fingerprint(['source',normalized_ref])
        row=c.execute(t.insert().values(project_id=p.id,cost_entry_id=bucket(c,a,p.id,command.type,start,category),created_by=a.person,
            type=command.type,source_type=command.type,amount_signed=amount,period=command.period,source_ref=ref,source_fingerprint=source,
            occurred_at=datetime(start.year,start.month,1,tzinfo=UTC),status='pending',correlation_id=UUID(rid)).returning(t)).mappings().one()
        return receipt(c,a,op,key,rid,p.id,sig,row)
    return run(authorization,op,write)


def _week_range(report):
    return (datetime.combine(report.period_start, time.min, reports_service.TZ),
            datetime.combine(report.period_end, time.min, reports_service.TZ))


def _weekly_source(report) -> str:
    return f'{WEEKLY_SOURCE_PREFIX}{report.id}@{report.revision}'


def _weekly_fingerprint(source: str, amount: Decimal, period: str) -> str:
    return fingerprint(['weekly-report-effort-v1', source, format(amount, '.2f'), period])


def _bound_weekly_posting(posting) -> bool:
    return (posting.type == 'effort' and posting.posting_of_id is None and posting.source_ref.startswith(WEEKLY_SOURCE_PREFIX)
            and posting.source_fingerprint == _weekly_fingerprint(posting.source_ref, posting.amount_signed, posting.period))


def _bound_report_version(posting) -> tuple[UUID, int] | None:
    if not _bound_weekly_posting(posting):
        return None
    match = WEEKLY_SOURCE.fullmatch(posting.source_ref)
    if match is None:
        return None
    try:
        return UUID(match[1]), int(match[2])
    except ValueError:
        return None


def _weekly_effort(c, report, now) -> tuple[Decimal, Decimal, Decimal]:
    fields = reports_service.current_publication_fields(report, now)
    if report.type != 'weekly' or fields is None:
        raise Failure(422, 'WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION', project_id=report.project_id)
    lower, upper = _week_range(report)
    q = t.c
    verified = c.scalar(sa.select(sa.func.coalesce(sa.func.sum(q.amount_signed), 0)).where(
        q.project_id == report.project_id, q.type == 'effort', q.status == 'verified',
        q.verified_at <= now, q.occurred_at <= now, q.occurred_at >= lower, q.occurred_at < upper))
    declared = Decimal(fields.effort_person_days)
    return declared, verified, max(Decimal(0), declared - max(Decimal(0), verified))


def _allowed_periods(report, now) -> list[str]:
    lower, upper = _week_range(report)
    if lower > now:
        return []
    return sorted({day.astimezone(UTC).strftime('%Y-%m') for day in
                   (lower, min(now, upper - timedelta(microseconds=1)))})


def _weekly_postings(c, report):
    q = t.c
    return [row for row in c.execute(sa.select(t).where(q.project_id == report.project_id, q.type == 'effort',
        q.posting_of_id.is_(None), q.source_ref == _weekly_source(report)).order_by(
        (q.status == 'verified').desc(), q.created_at.desc(), q.id)).mappings() if _bound_weekly_posting(row)]


@router.get('/reports/{reportId}/effort-posting', operation_id=WEEKLY_EFFORT_PREVIEW,
            response_model_exclude_none=True, openapi_extra=HUMAN)
def weekly_effort_preview(reportId: UUID, authorization: Auth) -> Result[WeeklyEffortPreview]:
    def read(c, a, _):
        p, report, pm = reports_service._context(c, a, report_id=reportId)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        declared, verified, remaining = _weekly_effort(c, report, now)
        candidates = _weekly_postings(c, report)
        existing = candidates[0] if candidates else None
        periods = _allowed_periods(report, now)
        return Result[WeeklyEffortPreview](data=WeeklyEffortPreview(
            reportRef=reports_service._ref(report), declaredPersonDays=format(declared, '.2f'),
            verifiedPersonDays=format(verified, '.2f'), candidatePersonDays=format(remaining, '.2f'),
            allowedPeriods=periods, existing=item(existing) if existing is not None else None,
            canPropose=pm and p.stage in OPEN and remaining > 0 and bool(periods)
            and (existing is None or existing.status != 'verified')),
            meta=meta(WEEKLY_EFFORT_PREVIEW, [reports_service._ref(report)]))
    return run(authorization, WEEKLY_EFFORT_PREVIEW, read)


@router.post('/reports/{reportId}/effort-posting', operation_id=WEEKLY_EFFORT_PROPOSE, openapi_extra=HUMAN)
def weekly_effort_propose(reportId: UUID, command: WeeklyEffortCandidate, authorization: Auth,
                          key: Key, expected: Revision) -> Result[Posting]:
    signature = fingerprint([str(reportId), int(expected), command.model_dump()])

    def write(c, a, rid):
        p, report, pm = reports_service._context(c, a, report_id=reportId, write=True)
        if not pm:
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        if prior := replay(c, a, WEEKLY_EFFORT_PROPOSE, key, signature, Result[Posting]):
            return prior
        if p.stage not in OPEN:
            raise Failure(422, 'LEDGER_LOCKED', project_id=p.id)
        if report.revision != int(expected):
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        _, _, remaining = _weekly_effort(c, report, now)
        if command.period not in _allowed_periods(report, now):
            raise Failure(422, 'WEEKLY_EFFORT_PERIOD_INVALID', project_id=p.id)
        source = _weekly_source(report)
        posting_hash = _weekly_fingerprint(source, Decimal(command.expectedAmount), command.period)
        candidates = _weekly_postings(c, report)
        if any(row.status == 'verified' for row in candidates):
            raise Failure(409, 'WEEKLY_EFFORT_ALREADY_VERIFIED', project_id=p.id)
        if remaining <= 0 or Decimal(command.expectedAmount) != remaining:
            raise Failure(409, 'WEEKLY_EFFORT_BALANCE_CHANGED', project_id=p.id)
        if existing := next((row for row in candidates if row.source_fingerprint == posting_hash), None):
            return receipt(c, a, WEEKLY_EFFORT_PROPOSE, key, rid, p.id, signature, existing)
        start = period_start(command.period)
        row = c.execute(t.insert().values(project_id=p.id, cost_entry_id=bucket(c, a, p.id, 'effort', start),
            created_by=a.person, type='effort', source_type='effort', amount_signed=remaining, period=command.period,
            source_ref=source, source_fingerprint=posting_hash, occurred_at=datetime.combine(start, time.min, UTC),
            status='pending', correlation_id=UUID(rid)).returning(t)).mappings().one()
        return receipt(c, a, WEEKLY_EFFORT_PROPOSE, key, rid, p.id, signature, row)
    return run(authorization, WEEKLY_EFFORT_PROPOSE, write)


def _check_weekly_verification(c, posting, when: datetime, now: datetime) -> None:
    if not _bound_weekly_posting(posting):
        return
    source = _bound_report_version(posting)
    if source is None:
        raise Failure(422, 'WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION', project_id=posting.project_id)
    r = reports_service.r
    report = c.execute(sa.select(reports_service.reports).where(
        r.id == source[0], r.revision == source[1], r.project_id == posting.project_id)).mappings().one_or_none()
    if report is None:
        raise Failure(422, 'WEEKLY_EFFORT_REQUIRES_CURRENT_PUBLICATION', project_id=posting.project_id)
    _, _, remaining = _weekly_effort(c, report, now)
    lower, upper = _week_range(report)
    if not lower <= when < upper:
        raise Failure(422, 'WEEKLY_EFFORT_OCCURRENCE_OUTSIDE_WEEK', project_id=posting.project_id)
    if any(row.status == 'verified' for row in _weekly_postings(c, report)):
        raise Failure(409, 'WEEKLY_EFFORT_ALREADY_VERIFIED', project_id=posting.project_id)
    if remaining != posting.amount_signed:
        raise Failure(409, 'WEEKLY_EFFORT_BALANCE_CHANGED', project_id=posting.project_id)


@router.get('/projects/{projectId}/cost-postings',operation_id='human_cost_postings_list',response_model_exclude_none=True)
def listing(projectId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100),
            category: PostingFilter | None = None)->Result[Page]:
    from .category_postings import current_categories, pending
    from .policy import enabled
    from .requirement_delivery import can_deliver

    op='human_cost_postings_list'
    def read(c,a,_):
        p=project(c,a,projectId); allowed=finance(c,a) and p.stage in OPEN
        reverse=t.alias(); linked=sa.exists(sa.select(reverse.c.id).where(reverse.c.posting_of_id==t.c.id))
        e = db.cost_entries
        source = t.outerjoin(e, sa.and_(e.c.id == t.c.cost_entry_id, e.c.project_id == t.c.project_id))
        assigned = current_categories(p.id)
        source = source.outerjoin(assigned, assigned.c.posting_id == sa.func.coalesce(t.c.posting_of_id, t.c.id))
        effective_category = sa.func.coalesce(assigned.c.category, e.c.category)
        conditions = [t.c.project_id == p.id]
        if category == 'uncategorized':
            conditions.append(sa.or_(effective_category.is_(None), effective_category.not_in(CATEGORY_KIND)))
        elif category is not None:
            conditions.append(effective_category == category)
        stmt=sa.select(t,linked.label('has_reversal'),e.c.category.label('original_category'),
            effective_category.label('category'), assigned.c.id.label('classification_id')).select_from(source).where(*conditions)
        total=c.scalar(sa.select(sa.func.count()).select_from(source).where(*conditions))
        rows = c.execute(stmt.order_by(t.c.created_at.desc(), t.c.id).offset(pg_offset(page, pageSize))
                         .limit(pageSize)).mappings().all()
        can_classify = p.stage in OPEN and can_deliver(c, a, p)
        can_verify_classification = allowed and enabled(c, p, 'cost.category')
        candidates = pending(c, p.id, [row.id for row in rows]) if rows and can_verify_classification else {}
        report_versions = {row.id: _bound_report_version(row) for row in rows}
        wanted = {version for version in report_versions.values() if version is not None}
        r = reports_service.r
        weeks = {(entry.id, entry.revision): (entry.period_start, entry.period_end - timedelta(days=1))
            for entry in c.execute(sa.select(r.id, r.revision, r.period_start, r.period_end)
                .where(r.project_id == p.id, r.type == 'weekly', sa.tuple_(r.id, r.revision).in_(wanted)))} if wanted else {}
        items=[]
        for row in rows:
            verified=row.status=='verified'
            report_version = report_versions[row.id]
            proposed = candidates.get(row.id)
            if proposed and a.person in (proposed.created_by, row.created_by):
                proposed = None
            start, end = weeks.get(report_version, (None, None)) if report_version is not None else (None, None)
            items.append(Detail(**item(row).model_dump(),revision=2 if verified else 1,period=row.period,
                category=row.category if row.category in CATEGORY_KIND else None,
                occurredAt=row.occurred_at if verified or row.posting_of_id else None,verifiedAt=row.verified_at,postingOfId=row.posting_of_id,
                reversalReason=row.reversal_reason,verificationNote=row.verification_note,
                reportWeekStart=start,reportWeekEnd=end,
                classificationId=row.classification_id,
                canClassify=True if can_classify and verified and not row.posting_of_id and row.original_category not in CATEGORY_KIND else None,
                pendingClassificationId=proposed.id if proposed else None,
                pendingCategory=proposed.category if proposed else None,
                pendingClassificationReason=proposed.reason if proposed else None,
                canVerify=allowed and not verified and row.created_by!=a.person,
                canReverse=allowed and verified and row.amount_signed>0 and not row.has_reversal))
        return Result[Page](data=Page(items=items,page=page,pageSize=pageSize,total=total,canPropose=p.stage in OPEN and can_manage(c,a,p)),
            meta=meta(op,[f'cost-posting:{i.postingId}@{i.revision}' for i in items]))
    return run(authorization,op,read)


def decide(auth,posting_id,command,expected,key,*,reverse):
    op='human_reverse_cost_posting' if reverse else 'human_verify_cost_posting'
    parameters=command.model_dump(mode='json')
    if parameters.get('evidenceRef') is None:
        parameters.pop('evidenceRef',None)
    sig=fingerprint([str(posting_id),expected,parameters])
    def write(c,a,rid):
        if not finance(c,a): raise Failure(403,'FORBIDDEN')
        pid=c.scalar(sa.select(t.c.project_id).join(db.projects,db.projects.c.id==t.c.project_id).where(t.c.id==posting_id,visible(a)))
        p=project(c,a,pid,lock=True)
        row=c.execute(sa.select(t).where(t.c.project_id==pid,t.c.id==posting_id).with_for_update()).mappings().one()
        old=replay(c,a,op,key,sig,Result[Posting])
        if old: return old
        if p.stage not in OPEN: raise Failure(422,'LEDGER_LOCKED')
        if expected!=(2 if row.status=='verified' else 1): raise Failure(409,'VERSION_CONFLICT')
        reason=command.reason.strip()
        if len(reason)<4: raise Failure(422,'INVALID_REASON')
        evidence_ref=None
        if reverse:
            if row.status!='verified' or row.amount_signed<=0: raise Failure(422,'INVALID_REVERSAL')
            if c.scalar(sa.select(t.c.id).where(t.c.posting_of_id==row.id).limit(1)): raise Failure(409,'REVERSAL_EXISTS')
            stmt=t.insert().values(project_id=pid,cost_entry_id=row.cost_entry_id,created_by=a.person,type=row.type,source_type=row.type,
                amount_signed=-row.amount_signed,period=row.period,source_ref=row.source_ref,source_fingerprint=fingerprint(['reversal',str(row.id)]),
                occurred_at=row.occurred_at,status='pending',posting_of_id=row.id,reversal_reason=reason,correlation_id=UUID(rid))
        else:
            if row.status=='verified': raise Failure(409,'ALREADY_VERIFIED')
            if row.created_by==a.person: raise Failure(403,'INDEPENDENT_VERIFIER_REQUIRED')
            when=row.occurred_at if row.posting_of_id else command.occurredAt
            if when is None: raise Failure(422,'OCCURRENCE_REQUIRED')
            if row.posting_of_id and command.occurredAt and when!=command.occurredAt: raise Failure(422,'REVERSAL_DATE_LOCKED')
            now=c.scalar(sa.select(sa.func.clock_timestamp()))
            if when>now or when.astimezone(UTC).strftime('%Y-%m')!=row.period: raise Failure(422,'INVALID_OCCURRENCE')
            if not row.source_fingerprint: raise Failure(422,'SOURCE_REQUIRED')
            _check_weekly_verification(c, row, when, now)
            if command.evidenceRef:
                if row.posting_of_id:
                    raise Failure(422,'REVERSAL_EVIDENCE_INHERITED')
                try:
                    evidence_id=UUID(command.evidenceRef.removeprefix('evidence:'))
                except ValueError as exc:
                    raise Failure(422,'INVALID_COST_EVIDENCE_REF') from exc
                evidence=lookup_for_integrity(c,pid,evidence_id)
                if (evidence.resource_type,evidence.resource_id)!=('cost_posting',row.id):
                    raise Failure(422,'EVIDENCE_TARGET_MISMATCH')
                require_verified_content(evidence)
                evidence_ref=f'evidence:{evidence.id}'
            if c.scalar(sa.select(t.c.id).where(t.c.project_id==pid,t.c.type==row.type,t.c.status=='verified',t.c.source_fingerprint==row.source_fingerprint)):
                raise Failure(409,'SOURCE_ALREADY_VERIFIED')
            stmt=t.update().where(t.c.id==row.id).values(verified_by=a.person,verified_at=now,verification_note=reason,occurred_at=when,status='verified')
        saved=c.execute(stmt.returning(t)).mappings().one()
        return receipt(c,a,op,key,rid,pid,sig,saved,fingerprint(item(row).model_dump(mode='json')),evidence_ref=evidence_ref)
    return run(auth,op,write)


@router.post('/cost-postings/{postingId}/verify',operation_id='human_verify_cost_posting')
def verify(postingId:UUID,command:Verify,authorization:Auth,key:Key,expected:Revision)->Result[Posting]:
    return decide(authorization,postingId,command,int(expected),key,reverse=False)


@router.post('/cost-postings/{postingId}/reverse',operation_id='human_reverse_cost_posting')
def reverse(postingId:UUID,command:Reason,authorization:Auth,key:Key,expected:Revision)->Result[Posting]:
    return decide(authorization,postingId,command,int(expected),key,reverse=True)
