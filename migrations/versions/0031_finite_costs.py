"""Reject non-finite cost values without altering existing ledger records."""
from alembic import op

revision = '0031'
down_revision = '0030'

CHECKS = (
    ('cost_entries', ('budget', 'actual', 'forecast_remaining')),
    ('cost_postings', ('amount_signed',)),
    ('cost_plan_versions', ('money_budget', 'effort_budget', 'money_remaining', 'effort_remaining')),
    ('cost_baseline_history', ('budget', 'forecast_remaining')),
)


def upgrade():
    for table, fields in CHECKS:
        # PostgreSQL NUMERIC permits NaN, even with a precision, scale and nonnegative CHECK.
        # Validate existing rows too; any violation rolls back the entire revision.
        valid = ' AND '.join(f"{field} NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)"
                             for field in fields)
        op.create_check_constraint(f'ck_{table}_finite', table, valid)


def downgrade():
    for table, _ in reversed(CHECKS):
        op.drop_constraint(f'ck_{table}_finite', table, type_='check')
