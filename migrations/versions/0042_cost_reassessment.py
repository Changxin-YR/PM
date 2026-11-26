"""Preserve the written material and human decision for each cost reassessment."""
import sqlalchemy as sa
from alembic import op

revision = '0042'
down_revision = '0041'


def upgrade():
    op.execute('''CREATE TABLE cost_reassessment_records (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id uuid NOT NULL REFERENCES projects(id),
        task_id uuid NOT NULL REFERENCES tasks(id),
        task_revision bigint NOT NULL CHECK (task_revision > 0),
        event_id uuid NOT NULL REFERENCES rule_events(id),
        actor_id uuid NOT NULL REFERENCES persons(id),
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        request_id uuid NOT NULL UNIQUE,
        basis_hash char(64) NOT NULL CHECK (basis_hash ~ '^[a-f0-9]{64}$'),
        snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
        submission_id uuid UNIQUE REFERENCES cost_reassessment_records(id),
        decision_id uuid UNIQUE REFERENCES decisions(id),
        CHECK ((submission_id IS NULL) = (decision_id IS NULL)),
        UNIQUE (task_id, task_revision)
    )''')
    op.execute('''CREATE TRIGGER immutable_cost_reassessment_records
        BEFORE UPDATE OR DELETE OR TRUNCATE ON cost_reassessment_records
        FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE cost_reassessment_records IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM cost_reassessment_records)')):
        raise RuntimeError('Refusing destructive downgrade of cost reassessment history')
    op.drop_table('cost_reassessment_records')
