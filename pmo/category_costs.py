"""Category cost versions share the original ledger and approved total budgets."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import AwareDatetime, Field
from sqlalchemy.dialects.postgresql import JSONB

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .cost_categories import CATEGORY_KIND, CategoryAmounts, CategoryEditor, CategoryLine, CategorySummary
from .costs import FIELDS, cost_basis, latest_plan, plans
from .identity import Failure, visible
from .materials import finish, replay
from .policy import enabled, environment, events as policy_events, isolated_schema, require as require_policy, versions as policies
from .projects import fingerprint, meta, run
from .risks import WRITABLE, can_manage

router = APIRouter(prefix='/api/v1', tags=['Costs'])
HUMAN = {'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True}
BUDGET_DRAFT = 'human_category_cost_budget_draft'
FORECAST = 'human_category_cost_forecast'
KINDS = ('money', 'effort')
DRAFTING = ('drafting', 'designing')
UNCATEGORIZED = 'historical_uncategorized'


def version_table(name, *extra):
    return sa.table(name, sa.column('project_id', sa.Uuid), sa.column('revision', sa.BigInteger),
        sa.column('amounts', JSONB), sa.column('created_by', sa.Uuid),
        sa.column('effective_at', sa.DateTime(timezone=True)), *extra)


budgets = version_table('category_cost_budgets', sa.column('phase'), sa.column('source_ref'), sa.column('totals_ref'))
forecasts = version_table('category_cost_forecasts', sa.column('observed_at', sa.DateTime(timezone=True)),
    sa.column('budget_revision', sa.BigInteger), sa.column('budget_hash'), sa.column('actual_hash'),
    sa.column('actual_refs', JSONB), sa.column('plan_revision', sa.BigInteger), sa.column('reason'))


class Allocation(Strict):
    amounts: CategoryAmounts


class Forecast(Allocation):
    reason: str = Field(min_length=3, max_length=2000, pattern=r'\S')
    observedAt: AwareDatetime | None = None


class Version(Strict):
    projectId: UUID
    revision: int
    amounts: CategoryAmounts
    effectiveAt: datetime


def clock(c):
    return c.scalar(sa.select(sa.func.clock_timestamp()))


def revision(row):
    return row.revision if row else 1


def latest(c, table, pid, at=None, *, phase=None, initial=False):
    query = sa.select(table).where(table.c.project_id == pid)
    if at is not None:
        query = query.where(table.c.effective_at <= at)
    if phase:
        query = query.where(table.c.phase == phase)
    order = table.c.revision if initial else table.c.revision.desc()
    return c.execute(query.order_by(order).limit(1)).mappings().one_or_none()


def budget_ref(pid, row):
    return f'category-budget:{pid}@{row.revision}'


def forecast_ref(pid, row):
    return f'category-forecast:{pid}@{row.revision}'


def format_amount(value):
    return format(value if value else Decimal(0), '.2f')


def allocation(raw):
    if set(raw) != set(CATEGORY_KIND):
        raise Failure(422, 'CATEGORY_ALLOCATION_INCOMPLETE')
    amounts = {category: format_amount(Decimal(raw[category])) for category in CATEGORY_KIND}
    totals = {kind: sum((Decimal(value) for category, value in amounts.items()
                        if CATEGORY_KIND[category] == kind), Decimal(0)) for kind in KINDS}
    if any(total >= Decimal(10**16) for total in totals.values()):
        raise Failure(422, 'COST_TOTAL_OUT_OF_RANGE')
    return amounts, totals


def locked_project(c, a, pid):
    p = db.projects
    project = c.execute(sa.select(p).where(p.c.id == pid, visible(a)).with_for_update()).mappings().one_or_none()
    if project is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    if a.kind != 'human':
        raise Failure(403, 'FORBIDDEN', project_id=pid)
    return project


def version(row):
    return Version(projectId=row.project_id, revision=row.revision, amounts=row.amounts, effectiveAt=row.effective_at)


def append_plan(c, project, actor, totals, at, *, budget, reason=None):
    last = c.execute(latest_plan(project.id)).mappings().one_or_none()
    values = {field: last[field] if last else None for field in FIELDS}
    changed = 'budget' if budget else 'remaining'
    values.update({f'{kind}_{changed}': total for kind, total in totals.items()})
    new_revision = revision(last) + 1
    c.execute(plans.insert().values(project_id=project.id, revision=new_revision, **values,
        changed_field=f'money_{changed}', created_by=actor.person, reason=reason, effective_at=at))
    return new_revision


def existing_baseline(c, pid):
    t = db.cost_entries
    return c.execute(sa.select(t).where(t.c.project_id == pid, t.c.is_current_baseline)
        .order_by(t.c.type, t.c.id)).mappings().all()


def reject_aggregate_write(c, p, *, budget):
    if (latest(c, budgets, p.id) or (not budget and latest(c, forecasts, p.id))
            or required_for_project(c, p)):
        raise Failure(422, 'CATEGORY_ALLOCATION_REQUIRED', project_id=p.id)


def required_for_project(c, p):
    """Only projects created after the scoped policy cutover must start classified."""
    target = environment()
    if target is None:
        return False
    v, e = policies.c, policy_events.c
    org_cutover = sa.and_(v.project_id.is_(None), e.recorded_at <= p.created_at, v.effective_from <= p.created_at)
    project_cutover = sa.and_(v.project_id == p.id, v.effective_from <= sa.func.clock_timestamp(),
        sa.not_(sa.exists(sa.select(1).select_from(db.cost_entries).where(
            db.cost_entries.c.project_id == p.id, db.cost_entries.c.is_current_baseline))))
    return bool(c.scalar(sa.select(sa.exists(sa.select(1).select_from(policies.join(policy_events, e.policy_id == v.id))
        .where(v.org_id == p.org_id, v.environment == target, v.synthetic == isolated_schema(c),
            v.decision_code == 'DEC-26', v.policy['actions'].contains(['cost.category']), e.event == 'active',
            sa.or_(org_cutover, project_cutover))))))


@router.put('/projects/{projectId}/cost-categories/budget-draft', operation_id=BUDGET_DRAFT, openapi_extra=HUMAN)
def save_budget(projectId: UUID, command: Allocation, authorization: Auth, key: Key, expected: Revision) -> Result[Version]:
    rev = int(expected)
    sig = fingerprint([str(projectId), rev, command.model_dump(mode='json')])

    def write(c, a, rid):
        p = locked_project(c, a, projectId)
        if not can_manage(c, a, p):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        prior = replay(c, a, BUDGET_DRAFT, key, sig, Result[Version])
        if prior:
            return prior
        if p.stage not in DRAFTING or existing_baseline(c, p.id):
            raise Failure(422, 'COST_PLAN_LOCKED', project_id=p.id)
        old = latest(c, budgets, p.id)
        if revision(old) != rev:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        amounts, totals = allocation(command.amounts)
        at = clock(c)
        plan_revision = append_plan(c, p, a, totals, at, budget=True)
        ref = f'cost-plan:{p.id}@{plan_revision}'
        row = c.execute(budgets.insert().values(project_id=p.id, revision=rev + 1, phase='draft', amounts=amounts,
            created_by=a.person, effective_at=at, source_ref=ref, totals_ref=ref).returning(*budgets.c)).mappings().one()
        return finish(c, a, BUDGET_DRAFT, key, rid, p.id, sig, Result[Version](
            data=version(row), meta=meta(BUDGET_DRAFT, [budget_ref(p.id, row), ref])))

    return run(authorization, BUDGET_DRAFT, write)


def classifications(c, pid, at):
    rows = c.execute(sa.text('''SELECT DISTINCT ON (v.posting_id) v.posting_id, v.id, v.effective_at, r.category
        FROM category_posting_classifications v JOIN category_posting_requests r ON r.id=v.request_id
        WHERE v.project_id=:pid AND v.effective_at<=:at
        ORDER BY v.posting_id, v.effective_at DESC, v.id DESC'''), {'pid': pid, 'at': at}).mappings()
    return {row.posting_id: row for row in rows}


def actuals(c, basis, at):
    overrides = classifications(c, basis.pid, at)
    amounts: dict[tuple[str, str], Decimal] = {}
    refs: dict[tuple[str, str], list[str]] = {}
    for posting in basis.verified:
        classification = overrides.get(posting.posting_of_id or posting.id)
        category = classification.category if classification else posting.category
        if CATEGORY_KIND.get(category) != posting.type:
            category = UNCATEGORIZED
        key = posting.type, category
        amounts[key] = amounts.get(key, Decimal(0)) + posting.amount_signed
        refs.setdefault(key, []).append(f'cost-posting:{posting.id}')
        if classification:
            refs[key].append(f'cost-classification:{classification.id}')
    return amounts, {key: sorted(set(values)) for key, values in refs.items()}


def actual_refs(refs):
    return sorted({ref for values in refs.values() for ref in values})


def budget_hash(basis, budget):
    if budget is not None and budget.phase == 'draft':
        return fingerprint([budget_ref(basis.pid, budget)])
    return fingerprint([budget_ref(basis.pid, budget) if budget else None,
                        *(basis.budget_refs(kind) for kind in KINDS)])


@router.put('/projects/{projectId}/cost-categories/forecast', operation_id=FORECAST, openapi_extra=HUMAN)
def save_forecast(projectId: UUID, command: Forecast, authorization: Auth, key: Key, expected: Revision) -> Result[Version]:
    rev = int(expected)
    sig = fingerprint([str(projectId), rev, command.model_dump(mode='json')])

    def write(c, a, rid):
        p = locked_project(c, a, projectId)
        if not can_manage(c, a, p):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        prior = replay(c, a, FORECAST, key, sig, Result[Version])
        if prior:
            return prior
        if p.stage not in WRITABLE:
            raise Failure(422, 'COST_PLAN_LOCKED', project_id=p.id)
        draft = latest(c, budgets, p.id)
        if draft is None:
            raise Failure(422, 'CATEGORY_BUDGET_REQUIRED', project_id=p.id)
        if draft.phase == 'approved':
            require_policy(c, p, 'cost.category', rid)
        old = latest(c, forecasts, p.id)
        if revision(old) != rev:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        amounts, totals = allocation(command.amounts)
        reason = command.reason.strip()
        if len(reason) < 3:
            raise Failure(422, 'INVALID_REASON', project_id=p.id)
        now = clock(c)
        observed = command.observedAt or now
        if not p.created_at <= observed <= now:
            raise Failure(422, 'INVALID_FORECAST_AS_OF', project_id=p.id)
        basis = cost_basis(c, p.id, observed)
        budget = latest(c, budgets, p.id, observed)
        _, posted_refs = actuals(c, basis, observed)
        refs = actual_refs(posted_refs)
        plan_revision = append_plan(c, p, a, totals, now, budget=False, reason=reason)
        row = c.execute(forecasts.insert().values(project_id=p.id, revision=rev + 1, amounts=amounts,
            observed_at=observed, effective_at=now, created_by=a.person, budget_revision=budget.revision if budget else None,
            budget_hash=budget_hash(basis, budget), actual_hash=fingerprint(refs), actual_refs=refs,
            plan_revision=plan_revision, reason=reason).returning(*forecasts.c)).mappings().one()
        source = [forecast_ref(p.id, row), f'cost-plan:{p.id}@{plan_revision}']
        return finish(c, a, FORECAST, key, rid, p.id, sig,
            Result[Version](data=version(row), meta=meta(FORECAST, source)))

    return run(authorization, FORECAST, write)


def freeze_material(c, p):
    draft = latest(c, budgets, p.id)
    if draft is None or draft.phase != 'draft':
        return None, None
    return draft, latest(c, forecasts, p.id)


def freeze(c, p, a, draft, decision_id, at):
    row = c.execute(budgets.insert().values(project_id=p.id, revision=draft.revision + 1, phase='approved',
        amounts=draft.amounts, created_by=a.person, effective_at=at, source_ref=f'decision:{decision_id}',
        totals_ref=draft.totals_ref).returning(*budgets.c)).mappings().one()
    return budget_ref(p.id, row)


def initial_totals(c, pid, at):
    rows = c.execute(sa.text('''SELECT DISTINCT ON (h.entry_id) h.entry_id, h.seq, h.type, h.budget
        FROM cost_baseline_history h JOIN cost_entries e ON e.id=h.entry_id AND e.project_id=h.project_id
        WHERE h.project_id=:pid AND h.is_current AND h.effective_at<=:at
          AND e.baseline_version=1 AND e.category<>'posting_ledger'
        ORDER BY h.entry_id, h.effective_at, h.seq'''), {'pid': pid, 'at': at}).mappings()
    totals: dict[str, Decimal] = {}
    refs: dict[str, list[str]] = {}
    for row in rows:
        totals[row.type] = totals.get(row.type, Decimal(0)) + row.budget
        refs.setdefault(row.type, []).append(f'cost-baseline-history:{row.entry_id}@{row.seq}')
    return totals, refs


def summary(c, a, p, basis, at, *, editor):
    approved = latest(c, budgets, p.id, at, phase='approved')
    may_read_draft = editor or (a.kind == 'human' and can_manage(c, a, p))
    draft = latest(c, budgets, p.id, at) if may_read_draft else None
    budget = approved or draft
    forecast = latest(c, forecasts, p.id, at) if approved or may_read_draft else None
    first = latest(c, budgets, p.id, at, phase='approved', initial=True) if approved else None
    initial = first if first and first.source_ref.startswith('decision:') else None
    net, posting_refs = actuals(c, basis, at)
    categorized_actual = any(category != UNCATEGORIZED for _, category in net)
    if a.kind == 'agent' and not approved and not categorized_actual:
        return None, None, []
    old_totals, old_refs = initial_totals(c, p.id, at)
    rows = []
    refs: list[str] = []
    for kind in KINDS:
        total, _, remaining = basis.amounts(kind)
        if total is None and may_read_draft and basis.plan:
            total = basis.plan[kind + '_budget']
        # Legacy totals are shown as unallocated. Absent forecasts stay unknown.
        known_remaining = basis.plan[kind + '_remaining'] if basis.plan else None
        if known_remaining is None and any(row.type == kind for row in basis.budgets):
            known_remaining = remaining
        for category in [*(key for key, unit in CATEGORY_KIND.items() if unit == kind), UNCATEGORIZED]:
            key = kind, category
            actual = net.get(key, Decimal(0))
            current = initial_amount = estimate = None
            sources = list(posting_refs.get(key, []))
            if category == UNCATEGORIZED:
                if not initial:
                    initial_amount = old_totals.get(kind)
                    sources.extend(old_refs.get(kind, []))
                if not budget:
                    current = total
                    sources.extend(basis.budget_refs(kind))
                if not forecast:
                    estimate = known_remaining
                    if estimate is not None:
                        sources.extend(basis.refs(kind)[:1] if basis.plan else basis.budget_refs(kind))
            else:
                if initial:
                    initial_amount = Decimal(initial.amounts[category])
                    sources.append(budget_ref(p.id, initial))
                if budget:
                    current = Decimal(budget.amounts[category])
                    sources.append(budget_ref(p.id, budget))
                if forecast:
                    estimate = Decimal(forecast.amounts[category])
                    sources.append(forecast_ref(p.id, forecast))
            if category == UNCATEGORIZED and forecast:
                estimate = Decimal(0)
            if category == UNCATEGORIZED and budget:
                current = Decimal(0)
            if category == UNCATEGORIZED and initial:
                initial_amount = Decimal(0)
            projected = actual + estimate if estimate is not None else None
            variance = (projected - current) / current if projected is not None and current else None
            sources = list(dict.fromkeys(sources))
            refs.extend(sources)
            rows.append(CategoryLine(type=kind, category=category, actual=format_amount(actual),
                initialBudget=format_amount(initial_amount) if initial_amount is not None else None,
                budget=format_amount(current) if current is not None else None,
                forecastRemaining=format_amount(estimate) if estimate is not None else None,
                projectedTotal=format_amount(projected) if projected is not None else None,
                varianceRate=format(variance, '.4f') if variance is not None else None,
                unbudgetedExposure=current == 0 and (actual != 0 or bool(estimate)), sourceRefs=sources))
    mode: Literal['legacy', 'draft', 'classified'] = 'classified' if approved else 'draft' if draft else 'legacy'
    stale = (forecast.actual_hash != fingerprint(actual_refs(posting_refs)) or
             forecast.budget_hash != budget_hash(basis, budget)) if forecast else None
    result = CategorySummary(items=rows, mode=mode, budgetRevision=revision(budget), forecastRevision=revision(forecast),
        forecastObservedAt=forecast.observed_at if forecast else None, forecastEffectiveAt=forecast.effective_at if forecast else None,
        forecastNeedsReview=stale)
    refs = refs if budget or categorized_actual else []
    if not editor:
        return result, None, refs
    can_change = p.stage in ('executing', 'on_hold', 'delivering') and bool(basis.budgets)
    editor_state = CategoryEditor(canSaveBudget=p.stage in DRAFTING and not basis.budgets,
        canSaveForecast=bool(budget) and p.stage in WRITABLE and (not approved or enabled(c, p, 'cost.category')),
        changeRevision=revision(approved) if can_change else None,
        draftAmounts=budget.amounts if budget else None, forecastAmounts=forecast.amounts if forecast else None)
    return result, editor_state, refs
