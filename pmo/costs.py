from decimal import Decimal
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import AwareDatetime, Field

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import RULE_VERSION, Result, Strict
from .cost_math import cost_line
from .identity import Failure, visible
from .materials import finish, project_access, replay
from .projects import fingerprint, meta, run
from .risks import WRITABLE, can_manage

router = APIRouter(prefix='/api/v1', tags=['Costs'])
Amount = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$')]
Kind = Literal['money', 'effort']
FIELDS = ('money_budget', 'effort_budget', 'money_remaining', 'effort_remaining')
plans = sa.table('cost_plan_versions', sa.column('project_id',sa.Uuid), sa.column('revision',sa.BigInteger),
    *(sa.column(k,sa.Numeric(18,2)) for k in FIELDS), sa.column('changed_field'), sa.column('reason'),
    sa.column('created_by',sa.Uuid),sa.column('effective_at',sa.DateTime(timezone=True)))


class Budget(Strict):
    type: Kind
    budget: Amount


class Forecast(Strict):
    type: Kind
    forecastRemaining: Amount
    reason: str = Field(min_length=3,max_length=2000,pattern=r'\S')


class Plan(Strict):
    projectId: UUID
    revision: int
    moneyBudget: str | None = None
    effortBudget: str | None = None
    moneyForecastRemaining: str | None = None
    effortForecastRemaining: str | None = None


def plan_view(pid, row):
    fields = dict(zip(FIELDS, ('moneyBudget','effortBudget','moneyForecastRemaining','effortForecastRemaining')))
    return Plan(projectId=pid,revision=row.revision if row else 1,
                **{name:format(row[key],'.2f') for key,name in fields.items() if row and row[key] is not None})


def latest_plan(pid):
    return sa.select(plans).where(plans.c.project_id==pid).order_by(plans.c.revision.desc()).limit(1)


def save(auth,key,expected,pid,command):
    budget = isinstance(command,Budget)
    op = 'pmo_cost_budget_draft_save' if budget else 'pmo_cost_forecast_save'
    sig = fingerprint([str(pid),expected,command.model_dump()])
    def write(c,a,rid):
        p = db.projects
        project = c.execute(sa.select(p).where(p.c.id==pid,visible(a)).with_for_update()).mappings().one_or_none()
        if not project: raise Failure(404,'RESOURCE_NOT_FOUND')
        if not can_manage(c,a,project): raise Failure(403,'FORBIDDEN')
        prior = replay(c,a,op,key,sig,Result[Plan])
        if prior: return prior
        if project.stage not in (('drafting','designing') if budget else WRITABLE): raise Failure(422,'COST_PLAN_LOCKED')
        last = c.execute(latest_plan(pid)).mappings().one_or_none()
        before = plan_view(pid,last)
        if before.revision!=expected: raise Failure(409,'VERSION_CONFLICT')
        values = {k:last[k] if last else None for k in FIELDS}
        field = command.type + ('_budget' if budget else '_remaining')
        values[field] = Decimal(command.budget if budget else command.forecastRemaining)
        reason = None if budget else command.reason.strip()
        if reason is not None and len(reason)<3: raise Failure(422,'INVALID_REASON')
        row = c.execute(plans.insert().values(**values,project_id=pid,revision=expected+1,created_by=a.person,
            changed_field=field,reason=reason).returning(*plans.c)).mappings().one()
        item = plan_view(pid,row)
        return finish(c,a,op,key,rid,pid,sig,Result[Plan](data=item,meta=meta(op,[f'cost-plan:{pid}@{row.revision}'])),
                      fingerprint(before.model_dump(mode='json',exclude_none=True)))
    return run(auth,op,write)


@router.put('/projects/{projectId}/cost-budgets',operation_id='pmo_cost_budget_draft_save',response_model_exclude_none=True)
def save_budget(projectId:UUID,command:Budget,authorization:Auth,key:Key,expected:Revision)->Result[Plan]:
    return save(authorization,key,int(expected),projectId,command)


@router.put('/projects/{projectId}/cost-forecasts',operation_id='pmo_cost_forecast_save',response_model_exclude_none=True)
def save_forecast(projectId:UUID,command:Forecast,authorization:Auth,key:Key,expected:Revision)->Result[Plan]:
    return save(authorization,key,int(expected),projectId,command)


class Line(Strict):
    budget: str
    actual: str
    forecastRemaining: str
    projectedTotal: str
    varianceRate: str | None
    color: str


class Summary(Strict):
    projectId: UUID
    money: Line
    effort: Line
    asOf: datetime
    ruleVersion: str
    plan: Plan | None = None  # REST editor only
    canSaveBudget: bool | None = None
    canSaveForecast: bool | None = None


@router.get('/projects/{projectId}/costs',operation_id='pmo_cost_summary',response_model_exclude_none=True)
def summary(projectId:UUID,authorization:Auth,asOf:AwareDatetime|None=None)->Result[Summary]:
    op = 'pmo_cost_summary'
    def query(c,a,_):
        project = project_access(c,a,projectId)
        now = c.scalar(sa.select(sa.func.clock_timestamp()))
        at = asOf or now
        if at>now: raise Failure(422,'FUTURE_AS_OF')
        if at<project.created_at: raise Failure(404,'RESOURCE_NOT_FOUND')
        plan = c.execute(latest_plan(projectId).where(plans.c.effective_at<=at)).mappings().one_or_none()
        refs = [f'project:{projectId}']
        if plan: refs.append(f'cost-plan:{projectId}@{plan.revision}')
        # First pick each entry's last state at the cutoff, THEN drop retired entries.
        rows = c.execute(sa.text('''SELECT * FROM (SELECT DISTINCT ON(entry_id) * FROM cost_baseline_history
            WHERE project_id=:pid AND effective_at<=:at ORDER BY entry_id,effective_at DESC,seq DESC) s
            WHERE is_current ORDER BY entry_id'''),{'pid':projectId,'at':at}).mappings().all()
        postings = db.cost_postings.c
        verified = c.execute(sa.select(postings.id,postings.type,postings.amount_signed).where(postings.project_id==projectId,
            postings.status=='verified',postings.verified_at<=at,postings.occurred_at<=at).order_by(postings.id)).mappings().all()
        refs.extend(f'cost-entry:{r.entry_id}@{r.seq}' for r in rows)
        refs.extend(f'cost-posting:{r.id}' for r in verified)
        def line(kind):
            entries = [r for r in rows if r.type==kind]
            budget = sum((r.budget for r in entries),Decimal(0)) if entries else None
            actual = sum((r.amount_signed for r in verified if r.type==kind),Decimal(0))
            remaining = plan[kind+'_remaining'] if plan else None
            if remaining is None: remaining = sum((r.forecast_remaining for r in entries),Decimal(0))
            result = cost_line(budget,actual,remaining)
            return Line(budget=format(budget or Decimal(0),'.2f'),actual=format(actual,'.2f'),forecastRemaining=format(remaining,'.2f'),
                projectedTotal=format(result.projected,'.2f'),varianceRate=format(result.variance,'.4f') if result.variance is not None else None,color=result.color)
        editor = a.kind=='human' and asOf is None and can_manage(c,a,project)
        data = Summary(projectId=projectId,money=line('money'),effort=line('effort'),asOf=at,ruleVersion=RULE_VERSION,
                       plan=plan_view(projectId,plan) if a.kind=='human' else None,
                       canSaveBudget=editor and project.stage in ('drafting','designing') if a.kind=='human' else None,
                       canSaveForecast=editor and project.stage in WRITABLE if a.kind=='human' else None)
        info = meta(op,refs); info.asOf = at
        return Result[Summary](data=data,meta=info)
    return run(authorization,op,query)
