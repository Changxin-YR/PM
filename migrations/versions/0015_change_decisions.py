"""Explicit project approval grants and immutable change decision snapshots."""
from alembic import op
import sqlalchemy as sa

revision='0015'
down_revision='0014'


def upgrade():
    op.execute('''CREATE TABLE change_approval_grants(project_id uuid NOT NULL REFERENCES projects(id),
        person_id uuid NOT NULL REFERENCES persons(id), target_type text NOT NULL CHECK(target_type IN ('requirement','milestone','budget','charter')),
        authority_ref text NOT NULL CHECK(length(trim(authority_ref))>0), expires_at timestamptz NOT NULL, revoked_at timestamptz,
        PRIMARY KEY(project_id,person_id,target_type))''')
    op.execute('''CREATE TABLE change_decision_records(change_id uuid PRIMARY KEY REFERENCES change_entries(id),
        approver_id uuid NOT NULL REFERENCES persons(id),authority_ref text NOT NULL,rationale text NOT NULL,
        outcome text NOT NULL CHECK(outcome IN ('applied','rejected')), old_state jsonb NOT NULL,new_state jsonb,
        request_id uuid UNIQUE NOT NULL,decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK((outcome='applied')=(new_state IS NOT NULL)))''')
    op.execute('CREATE TRIGGER immutable_change_decisions BEFORE UPDATE OR DELETE OR TRUNCATE ON change_decision_records FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c=op.get_bind(); c.exec_driver_sql('LOCK TABLE change_approval_grants,change_decision_records IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM change_approval_grants) OR EXISTS(SELECT FROM change_decision_records) OR EXISTS(SELECT FROM approval_requests WHERE action=\'baseline_change\')')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.drop_table('change_decision_records'); op.drop_table('change_approval_grants')
