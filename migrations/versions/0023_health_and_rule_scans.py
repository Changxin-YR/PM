"""Durable three-line observations, appeal receipts and resumable scans."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0023'
down_revision = '0022'


def upgrade():
    op.add_column('rule_events', sa.Column('input_snapshot', JSONB))
    # A scan can observe a delayed legacy or re-baselined milestone without
    # inventing a forecast edit. When present, the existing forecast FK stands.
    op.alter_column('milestone_delay_episodes', 'opening_revision', nullable=True)
    op.add_column('health_appeals', sa.Column('appealed_at', sa.DateTime(timezone=True)))
    op.create_index('ux_health_appeal_once', 'health_appeals', ['snapshot_id'], unique=True,
                    postgresql_where=sa.text('appealed_at IS NOT NULL'))
    op.create_index('ix_health_history', 'health_snapshots', ['project_id', 'as_of', 'id'])
    op.execute('''CREATE TABLE health_appeal_decisions (
        appeal_id uuid PRIMARY KEY REFERENCES health_appeals(id),
        reviewer_id uuid NOT NULL REFERENCES persons(id),
        verdict varchar(20) NOT NULL CHECK(verdict IN ('upheld','recompute')),
        rationale text NOT NULL CHECK(length(btrim(rationale))>=5),
        reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        request_id uuid NOT NULL UNIQUE
    )''')
    op.execute('''CREATE TABLE health_zero_budgets (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL REFERENCES projects(id),
        kind varchar(10) NOT NULL CHECK(kind IN ('money','effort')),
        baseline_hash char(64) NOT NULL CHECK(baseline_hash ~ '^[a-f0-9]{64}$'),
        declared_by uuid NOT NULL REFERENCES persons(id), reason text NOT NULL CHECK(length(btrim(reason))>=5),
        declared_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(project_id,kind,baseline_hash)
    )''')
    op.execute('''CREATE TABLE project_rule_scan_state (
        project_id uuid PRIMARY KEY REFERENCES projects(id),
        dirty boolean NOT NULL DEFAULT true, last_daily_slot date, last_scanned_at timestamptz
    )''')
    op.execute('CREATE INDEX ix_rule_scan_dirty ON project_rule_scan_state(project_id) WHERE dirty')
    for table in ('health_snapshots', 'health_appeals', 'health_appeal_decisions', 'health_zero_budgets'):
        op.execute(f'''CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('''LOCK TABLE health_snapshots,health_appeals,health_appeal_decisions,health_zero_budgets,
        milestone_delay_episodes,rule_events,project_rule_scan_state IN ACCESS EXCLUSIVE MODE''')
    if c.scalar(sa.text('''SELECT EXISTS(SELECT FROM health_snapshots WHERE results ? 'publication')
        OR EXISTS(SELECT FROM health_appeals WHERE appealed_at IS NOT NULL)
        OR EXISTS(SELECT FROM health_appeal_decisions) OR EXISTS(SELECT FROM health_zero_budgets)
        OR EXISTS(SELECT FROM milestone_delay_episodes WHERE opening_revision IS NULL)
        OR EXISTS(SELECT FROM rule_events WHERE input_snapshot IS NOT NULL)
        OR EXISTS(SELECT FROM project_rule_scan_state)''')):
        raise RuntimeError('Refusing to discard health and rule scan history')
    for table in ('health_snapshots', 'health_appeals'):
        op.execute(f'DROP TRIGGER immutable_{table} ON {table}')
    op.drop_table('health_appeal_decisions')
    op.drop_table('health_zero_budgets')
    op.drop_table('project_rule_scan_state')
    op.drop_index('ix_health_history', table_name='health_snapshots')
    op.drop_index('ux_health_appeal_once', table_name='health_appeals')
    op.drop_column('health_appeals', 'appealed_at')
    op.alter_column('milestone_delay_episodes', 'opening_revision', nullable=False)
    op.drop_column('rule_events', 'input_snapshot')
