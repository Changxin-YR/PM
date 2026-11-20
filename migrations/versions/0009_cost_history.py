"""Keep planning inputs separate from approved entries and preserve point-in-time revisions."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'


def upgrade():
    op.execute('''CREATE TABLE cost_plan_versions (
        project_id uuid NOT NULL REFERENCES projects(id), revision bigint NOT NULL CHECK(revision >= 2),
        money_budget numeric(18,2) CHECK(money_budget >= 0), effort_budget numeric(18,2) CHECK(effort_budget >= 0),
        money_remaining numeric(18,2) CHECK(money_remaining >= 0), effort_remaining numeric(18,2) CHECK(effort_remaining >= 0),
        changed_field text NOT NULL CHECK(changed_field IN ('money_budget','effort_budget','money_remaining','effort_remaining')),
        reason text, created_by uuid NOT NULL REFERENCES persons(id), effective_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(project_id,revision), CHECK(changed_field NOT LIKE '%remaining' OR length(trim(reason))>=3 AND reason IS NOT NULL))''')
    op.execute('''CREATE TABLE cost_baseline_history (
        seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, entry_id uuid NOT NULL REFERENCES cost_entries(id),
        project_id uuid NOT NULL REFERENCES projects(id), type varchar(10) NOT NULL, budget numeric(18,2) NOT NULL,
        forecast_remaining numeric(18,2) NOT NULL, is_current boolean NOT NULL, effective_at timestamptz NOT NULL DEFAULT clock_timestamp())''')
    op.execute('CREATE INDEX ix_cost_history_project ON cost_baseline_history(project_id,entry_id,effective_at DESC,seq DESC)')
    op.execute('LOCK TABLE cost_entries IN SHARE ROW EXCLUSIVE MODE')
    # An existing snapshot has no provable prior approval time. Do not backdate it.
    op.execute('''INSERT INTO cost_baseline_history(entry_id,project_id,type,budget,forecast_remaining,is_current)
        SELECT id,project_id,type,budget,forecast_remaining,is_current_baseline FROM cost_entries''')
    op.execute('''CREATE FUNCTION pmo_snapshot_cost_entry() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        INSERT INTO cost_baseline_history(entry_id,project_id,type,budget,forecast_remaining,is_current)
        VALUES(NEW.id,NEW.project_id,NEW.type,NEW.budget,NEW.forecast_remaining,NEW.is_current_baseline); RETURN NEW; END $$''')
    op.execute('''CREATE TRIGGER snapshot_cost_entry AFTER INSERT OR UPDATE OF budget,forecast_remaining,is_current_baseline
        ON cost_entries FOR EACH ROW EXECUTE FUNCTION pmo_snapshot_cost_entry()''')
    for name in ('cost_plan_versions','cost_baseline_history'):
        op.execute(f'CREATE TRIGGER immutable_{name} BEFORE UPDATE OR DELETE OR TRUNCATE ON {name} FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE cost_entries,cost_plan_versions,cost_baseline_history IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM cost_plan_versions) OR EXISTS(SELECT FROM cost_baseline_history)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.execute('DROP TRIGGER snapshot_cost_entry ON cost_entries')
    op.execute('DROP FUNCTION pmo_snapshot_cost_entry()')
    op.drop_table('cost_baseline_history')
    op.drop_table('cost_plan_versions')
