"""Versioned forecast evidence and LR-03 alert episodes."""
from alembic import op
import sqlalchemy as sa

revision='0011'
down_revision='0010'


def upgrade():
    op.execute('''CREATE TABLE milestone_forecasts (
        milestone_id uuid NOT NULL REFERENCES milestones(id), revision bigint NOT NULL,
        planned_date date NOT NULL, prior_date date, new_date date NOT NULL, reason text NOT NULL CHECK(length(trim(reason))>=3),
        created_by uuid NOT NULL REFERENCES persons(id), request_id uuid NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(milestone_id,revision))''')
    op.execute('CREATE TRIGGER immutable_forecast BEFORE UPDATE OR DELETE OR TRUNCATE ON milestone_forecasts FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    op.execute('''CREATE TABLE milestone_delay_episodes(event_id uuid PRIMARY KEY REFERENCES rule_events(id),
        milestone_id uuid NOT NULL REFERENCES milestones(id), opening_revision bigint NOT NULL, closing_revision bigint,
        FOREIGN KEY(milestone_id,opening_revision) REFERENCES milestone_forecasts(milestone_id,revision))''')
    op.execute('CREATE UNIQUE INDEX one_open_delay ON milestone_delay_episodes(milestone_id) WHERE closing_revision IS NULL')
    op.execute('CREATE UNIQUE INDEX one_rule_notification ON notifications(rule_event_id,recipient_person_id) WHERE rule_event_id IS NOT NULL')


def downgrade():
    c=op.get_bind()
    c.exec_driver_sql('LOCK TABLE milestone_forecasts,milestone_delay_episodes IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM milestone_forecasts) OR EXISTS(SELECT FROM milestone_delay_episodes)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.execute('DROP INDEX one_rule_notification')
    op.drop_table('milestone_delay_episodes')
    op.drop_table('milestone_forecasts')
