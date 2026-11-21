"""Schema introduced after the frozen 0001 baseline."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.types import TypeEngine
from typing import Any, cast

from . import schema_v1 as db

scan_state = sa.table('project_rule_scan_state', sa.column('project_id', sa.Uuid),
                      sa.column('dirty', sa.Boolean), sa.column('last_daily_slot', sa.Date),
                      sa.column('last_scanned_at', sa.DateTime(timezone=True)))
appeals = sa.table('health_appeals', sa.column('id', sa.Uuid), sa.column('snapshot_id', sa.Uuid),
                   sa.column('appealed_by', sa.Uuid), sa.column('reason'), sa.column('appealed_at', sa.DateTime(timezone=True)))
appeal_decisions = sa.table('health_appeal_decisions', sa.column('appeal_id', sa.Uuid),
    sa.column('reviewer_id', sa.Uuid), sa.column('verdict'), sa.column('rationale'),
    sa.column('reviewed_at', sa.DateTime(timezone=True)), sa.column('request_id', sa.Uuid))
zero_budgets = sa.table('health_zero_budgets', sa.column('id', sa.Uuid), sa.column('project_id', sa.Uuid),
    sa.column('kind'), sa.column('baseline_hash'), sa.column('declared_by', sa.Uuid),
    sa.column('reason'), sa.column('declared_at', sa.DateTime(timezone=True)))
events = sa.table('rule_events', *(sa.column(c.name, cast(TypeEngine[Any], c.type)) for c in db.rule_events.c),
                  sa.column('input_snapshot', JSONB))
FEASIBILITY_OVERDUE = 'feasibility_condition_overdue'

SCAN_SOURCE_WRITES = frozenset({
    'pmo_review_submit', 'human_review_decide', 'human_review_withdraw', 'human_execute_transition',
    'human_decide_approval', 'pmo_milestone_forecast', 'human_milestone_complete', 'pmo_cost_forecast_save',
    'human_verify_cost_posting', 'human_customer_confirm', 'human_requirement_baseline_sign',
    'human_requirement_dispute_open', 'human_requirement_dispute_withdraw',
    'pmo_change_propose', 'human_requirement_addition_propose', 'human_change_approve', 'human_change_reject', 'human_evidence_verify',
    'pmo_report_submit', 'human_report_submit_existing', 'human_report_confirm', 'pmo_task_update',
    'human_retrospective_save', 'human_confirm_lesson', 'human_health_zero_budget', 'human_review_health_appeal',
})


def request_scan(c, pid):
    c.execute(insert(scan_state).values(project_id=pid, dirty=True)
              .on_conflict_do_update(index_elements=[scan_state.c.project_id], set_={'dirty': True},
                                     where=scan_state.c.dirty.is_(False)))
