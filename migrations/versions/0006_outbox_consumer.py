"""Durable local event consumption and delivery history; preserve pending rows."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'


def upgrade():
    op.execute('''CREATE TABLE outbox_attempts (
        event_id uuid NOT NULL REFERENCES outbox_events(id), attempt integer NOT NULL CHECK(attempt>0),
        started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
        outcome varchar(20) NOT NULL CHECK(outcome IN ('processing','sent','retry','lease_expired','dead_letter')),
        PRIMARY KEY(event_id,attempt))''')
    op.execute('''CREATE TABLE project_event_inbox (
        dedupe_key varchar(160) PRIMARY KEY, event_id uuid NOT NULL UNIQUE REFERENCES outbox_events(id),
        project_id uuid NOT NULL REFERENCES projects(id), event_type varchar(80) NOT NULL,
        received_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('CREATE TRIGGER immutable_event_inbox BEFORE UPDATE OR DELETE OR TRUNCATE ON project_event_inbox FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    op.execute("CREATE INDEX ix_outbox_due ON outbox_events(next_attempt_at,id) WHERE status='pending'")
    op.execute("CREATE INDEX ix_outbox_expired ON outbox_events(locked_until,id) WHERE status='processing'")
    op.execute("ALTER TABLE stakeholders ADD CONSTRAINT ck_stakeholder_type CHECK(type IN ('customer','partner','internal'))")


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE outbox_attempts,project_event_inbox IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM outbox_attempts) OR EXISTS(SELECT FROM project_event_inbox)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.drop_constraint('ck_stakeholder_type', 'stakeholders')
    op.drop_index('ix_outbox_expired')
    op.drop_index('ix_outbox_due')
    op.drop_table('project_event_inbox')
    op.drop_table('outbox_attempts')
