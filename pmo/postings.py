from calendar import monthrange
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from unicodedata import normalize
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import AwareDatetime, Field, model_validator

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .pagination import pg_offset
from .contracts import Result, Strict
from .costs import Kind
from .identity import Failure, visible
from .materials import finish, replay
from .projects import fingerprint, meta, run
from .risks import can_manage

router = APIRouter(prefix='/api/v1',tags=['Cost postings'])
t = db.cost_postings
SIGNED = r'^-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$'
OPEN = ('drafting','charter_review','designing','feasibility_review','executing','on_hold','delivering','closing_review')


Category = Literal['procurement', 'travel', 'cloud', 'allocation', 'development', 'implementation', 'management']
PostingFilter = Category | Literal['uncategorized']
CATEGORY_KIND: dict[str, Kind] = {
    'procurement': 'money', 'travel': 'money', 'cloud': 'money', 'allocation': 'money',
    'development': 'effort', 'implementation': 'effort', 'management': 'effort',
}
UNCATEGORIZED = 'posting_ledger'
CATEGORIZED_PROPOSE = 'human_categorized_cost_posting_propose'
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}


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
    canVerify: bool
    canReverse: bool


class Page(Strict):
    items: list[Detail]
    page: int
    pageSize: int
    total: int
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


def receipt(c,a,op,key,rid,pid,sig,row,before=None):
    out=item(row)
    return finish(c,a,op,key,rid,pid,sig,Result[Posting](data=out,meta=meta(op,[f'cost-posting:{row.id}@{2 if row.status=="verified" else 1}'])),before)


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
        source=fingerprint(['source',normalize('NFKC',' '.join(ref.split())).casefold()])
        row=c.execute(t.insert().values(project_id=p.id,cost_entry_id=bucket(c,a,p.id,command.type,start,category),created_by=a.person,
            type=command.type,source_type=command.type,amount_signed=amount,period=command.period,source_ref=ref,source_fingerprint=source,
            occurred_at=datetime(start.year,start.month,1,tzinfo=UTC),status='pending',correlation_id=UUID(rid)).returning(t)).mappings().one()
        return receipt(c,a,op,key,rid,p.id,sig,row)
    return run(authorization,op,write)


@router.get('/projects/{projectId}/cost-postings',operation_id='human_cost_postings_list',response_model_exclude_none=True)
def listing(projectId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100),
            category: PostingFilter | None = None)->Result[Page]:
    op='human_cost_postings_list'
    def read(c,a,_):
        p=project(c,a,projectId); allowed=finance(c,a) and p.stage in OPEN
        reverse=t.alias(); linked=sa.exists(sa.select(reverse.c.id).where(reverse.c.posting_of_id==t.c.id))
        e = db.cost_entries
        source = t.outerjoin(e, sa.and_(e.c.id == t.c.cost_entry_id, e.c.project_id == t.c.project_id))
        conditions = [t.c.project_id == p.id]
        if category == 'uncategorized':
            conditions.append(sa.or_(e.c.category.is_(None), e.c.category.not_in(CATEGORY_KIND)))
        elif category is not None:
            conditions.append(e.c.category == category)
        stmt=sa.select(t,linked.label('has_reversal'),e.c.category).select_from(source).where(*conditions)
        total=c.scalar(sa.select(sa.func.count()).select_from(source).where(*conditions))
        items=[]
        for row in c.execute(stmt.order_by(t.c.created_at.desc(),t.c.id).offset(pg_offset(page,pageSize)).limit(pageSize)).mappings():
            verified=row.status=='verified'
            items.append(Detail(**item(row).model_dump(),revision=2 if verified else 1,period=row.period,
                category=row.category if row.category in CATEGORY_KIND else None,
                occurredAt=row.occurred_at if verified or row.posting_of_id else None,verifiedAt=row.verified_at,postingOfId=row.posting_of_id,
                reversalReason=row.reversal_reason,verificationNote=row.verification_note,
                canVerify=allowed and not verified and row.created_by!=a.person,
                canReverse=allowed and verified and row.amount_signed>0 and not row.has_reversal))
        return Result[Page](data=Page(items=items,page=page,pageSize=pageSize,total=total,canPropose=p.stage in OPEN and can_manage(c,a,p)),
            meta=meta(op,[f'cost-posting:{i.postingId}@{i.revision}' for i in items]))
    return run(authorization,op,read)


def decide(auth,posting_id,command,expected,key,*,reverse):
    op='human_reverse_cost_posting' if reverse else 'human_verify_cost_posting'
    sig=fingerprint([str(posting_id),expected,command.model_dump(mode='json')])
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
            if c.scalar(sa.select(t.c.id).where(t.c.project_id==pid,t.c.type==row.type,t.c.status=='verified',t.c.source_fingerprint==row.source_fingerprint)):
                raise Failure(409,'SOURCE_ALREADY_VERIFIED')
            stmt=t.update().where(t.c.id==row.id).values(verified_by=a.person,verified_at=now,verification_note=reason,occurred_at=when,status='verified')
        saved=c.execute(stmt.returning(t)).mappings().one()
        return receipt(c,a,op,key,rid,pid,sig,saved,fingerprint(item(row).model_dump(mode='json')))
    return run(auth,op,write)


@router.post('/cost-postings/{postingId}/verify',operation_id='human_verify_cost_posting')
def verify(postingId:UUID,command:Verify,authorization:Auth,key:Key,expected:Revision)->Result[Posting]:
    return decide(authorization,postingId,command,int(expected),key,reverse=False)


@router.post('/cost-postings/{postingId}/reverse',operation_id='human_reverse_cost_posting')
def reverse(postingId:UUID,command:Reason,authorization:Auth,key:Key,expected:Revision)->Result[Posting]:
    return decide(authorization,postingId,command,int(expected),key,reverse=True)
