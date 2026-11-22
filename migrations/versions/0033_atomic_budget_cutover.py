"""Use one effective instant for all budget history rows in a write statement."""
from alembic import op

revision = '0033'
down_revision = '0032'


def upgrade():
    op.execute('''CREATE OR REPLACE FUNCTION pmo_snapshot_cost_entry() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        INSERT INTO cost_baseline_history(entry_id,project_id,type,budget,forecast_remaining,is_current,effective_at)
        VALUES(NEW.id,NEW.project_id,NEW.type,NEW.budget,NEW.forecast_remaining,NEW.is_current_baseline,statement_timestamp());
        RETURN NEW; END $$''')


def downgrade():
    op.execute('''CREATE OR REPLACE FUNCTION pmo_snapshot_cost_entry() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        INSERT INTO cost_baseline_history(entry_id,project_id,type,budget,forecast_remaining,is_current)
        VALUES(NEW.id,NEW.project_id,NEW.type,NEW.budget,NEW.forecast_remaining,NEW.is_current_baseline);
        RETURN NEW; END $$''')
