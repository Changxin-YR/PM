"""Use the existing independent budget approval for a complete category allocation."""
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .category_costs import (HUMAN, KINDS, Allocation, allocation, budget_ref, budgets, clock, existing_baseline,
                             format_amount, latest, locked_project, revision)
from .change_history import Entry, entry
from .change_intents import capture, snapshot
from .contracts import Result
from .costs import latest_plan
from .identity import Failure
from .materials import finish, replay
from .policy import freeze
from .projects import fingerprint, meta, run
from .requirement_delivery import can_deliver

router = APIRouter(prefix='/api/v1', tags=['Changes'])
PROPOSE = 'human_category_budget_change_propose'
OPEN = ('executing', 'on_hold', 'delivering')


class Proposal(Allocation):
    reason: str = Field(min_length=3, max_length=2000, pattern=r'\S')
    reestimate: str | None = Field(default=None, min_length=3, max_length=2000, pattern=r'\S')


def categorized(change):
    return change.target_type == 'budget' and isinstance(change.after, dict) and 'categoryBudgets' in change.after


def state(c, project):
    rows = existing_baseline(c, project.id)
    if {row.type for row in rows} != set(KINDS):
        raise Failure(422, 'NO_ACTIVE_BASELINE', project_id=project.id)
    budget = latest(c, budgets, project.id, phase='approved')
    totals = {kind: format_amount(sum((row.budget for row in rows if row.type == kind), Decimal(0))) for kind in KINDS}
    before = {'categoryBudgets': budget.amounts if budget else None, 'categoryBudgetRevision': revision(budget),
              'moneyBudget': totals['money'], 'effortBudget': totals['effort']}
    return rows, budget, before


def frozen_state(c, p, rows, before, *, reestimate):
    frozen = {'baseline': [snapshot(row) for row in rows], 'allocation': before}
    if reestimate:
        charter = c.execute(sa.select(db.charters).where(db.charters.c.project_id == p.id)).mappings().one_or_none()
        plan = c.execute(latest_plan(p.id)).mappings().one_or_none()
        frozen['charter'] = snapshot(charter) if charter else None
        frozen['forecastPlan'] = snapshot(plan) if plan else None
    return frozen


def describe(raw, reestimate):
    amounts, totals = allocation(raw)
    after = {'categoryBudgets': amounts, 'moneyBudget': format_amount(totals['money']),
             'effortBudget': format_amount(totals['effort'])}
    if reestimate:
        after['reestimate'] = reestimate
    return after


def aggregate_changed(before, after):
    return any(before[kind + 'Budget'] != after[kind + 'Budget'] for kind in KINDS)


def approval_target(c, p, change):
    try:
        rows, budget, before = state(c, p)
        after = describe(change.after['categoryBudgets'], change.after.get('reestimate'))
    except (Failure, ValueError, TypeError, ArithmeticError):
        raise Failure(409, 'APPROVAL_STALE', project_id=p.id) from None
    old = next((row for row in rows if row.id == change.target_id), None)
    if old is None or before != change.before or after != change.after or revision(budget) != change.base_revision:
        raise Failure(409, 'APPROVAL_STALE', project_id=p.id)
    return old, frozen_state(c, p, rows, before, reestimate=aggregate_changed(before, after))


@router.post('/projects/{projectId}/changes/category-budget', operation_id=PROPOSE,
             response_model_exclude_none=True, openapi_extra=HUMAN)
def propose(projectId: UUID, command: Proposal, authorization: Auth, key: Key, expected: Revision) -> Result[Entry]:
    rev = int(expected)
    sig = fingerprint([str(projectId), rev, command.model_dump(mode='json')])

    def write(c, a, rid):
        p = locked_project(c, a, projectId)
        if not can_deliver(c, a, p):
            raise Failure(403, 'FORBIDDEN', project_id=p.id)
        prior = replay(c, a, PROPOSE, key, sig, Result[Entry])
        if prior:
            return prior
        if p.stage not in OPEN:
            raise Failure(422, 'COST_PLAN_LOCKED', project_id=p.id)
        rows, budget, before = state(c, p)
        if revision(budget) != rev:
            raise Failure(409, 'VERSION_CONFLICT', project_id=p.id)
        reason = command.reason.strip()
        reestimate = command.reestimate.strip() if command.reestimate else None
        if len(reason) < 3:
            raise Failure(422, 'INVALID_REASON', project_id=p.id)
        after = describe(command.amounts, reestimate)
        changed = aggregate_changed(before, after)
        if changed and budget is None:
            raise Failure(422, 'CATEGORY_ACTIVATION_MUST_PRESERVE_TOTALS', project_id=p.id)
        if changed and (not reestimate or len(reestimate) < 3):
            raise Failure(422, 'BUDGET_REESTIMATE_REQUIRED', project_id=p.id)
        if before['categoryBudgets'] == after['categoryBudgets']:
            raise Failure(422, 'NO_CHANGE', project_id=p.id)
        ch = db.change_entries
        target = rows[0]
        row = c.execute(ch.insert().values(project_id=p.id, target_id=target.id, target_type='budget',
            before=before, after=after, base_revision=rev, reason=reason, status='pending',
            requested_by=a.person, created_by=a.person).returning(ch)).mappings().one()
        frozen = frozen_state(c, p, rows, before, reestimate=changed)
        capture(c, a, p, row, target, frozen=frozen)
        freeze(c, p, 'cost.category', row.id)
        return finish(c, a, PROPOSE, key, rid, p.id, sig, Result[Entry](
            data=entry(row), meta=meta(PROPOSE, [f'change:{row.id}@{row.revision}'])))

    return run(authorization, PROPOSE, write)


def replace_totals(c, p, a, change, changed):
    t = db.cost_entries
    cutover = sa.func.statement_timestamp()
    retired = t.update().where(t.c.project_id == p.id, t.c.is_current_baseline, t.c.type.in_(changed)).values(
        is_current_baseline=False, updated_at=cutover, revision=t.c.revision + 1).returning(
        t.c.type, t.c.period_start, t.c.period_end, t.c.forecast_remaining, t.c.baseline_version).cte('retired_category_totals')
    replacement = sa.select(sa.literal(p.id), retired.c.type, sa.literal('project_baseline'),
        sa.func.min(retired.c.period_start), sa.func.max(retired.c.period_end),
        sa.func.sum(retired.c.forecast_remaining), sa.literal(0), sa.literal(a.person),
        sa.case({kind: Decimal(change.after[kind + 'Budget']) for kind in changed}, value=retired.c.type),
        sa.func.max(retired.c.baseline_version) + 1, sa.literal(f'change:{change.id}'), cutover, cutover).group_by(retired.c.type)
    return c.execute(t.insert().from_select(('project_id', 'type', 'category', 'period_start', 'period_end',
        'forecast_remaining', 'actual', 'created_by', 'budget', 'baseline_version', 'source_ref',
        'created_at', 'updated_at'), replacement).returning(t)).mappings().all()


def apply(c, p, a, change):
    changed = [kind for kind in KINDS if change.before[kind + 'Budget'] != change.after[kind + 'Budget']]
    replacements = replace_totals(c, p, a, change, changed) if changed else []
    at = replacements[0].updated_at if replacements else clock(c)
    totals = [f'cost-entry:{row.id}@{row.revision}' for row in existing_baseline(c, p.id)]
    row = c.execute(budgets.insert().values(project_id=p.id, revision=change.base_revision + 1,
        phase='approved', amounts=change.after['categoryBudgets'], created_by=a.person, effective_at=at,
        source_ref=f'change:{change.id}', totals_ref=','.join(totals)).returning(*budgets.c)).mappings().one()
    return snapshot(row), [budget_ref(p.id, row), *totals], at
