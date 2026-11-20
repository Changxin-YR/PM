"""Bind private evidence to the exact resource and record independent decisions."""
from alembic import op
import sqlalchemy as sa

revision='0013'
down_revision='0012'


def upgrade():
    op.execute('''CREATE TABLE evidence_links(evidence_id uuid PRIMARY KEY REFERENCES evidence_files(id),
        resource_type text NOT NULL CHECK(resource_type IN ('charter','milestone','requirement','review','risk','report','cost_posting','decision')),
        resource_id uuid NOT NULL, description varchar(500) NOT NULL,revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1),verification_note varchar(1000))''')
    op.execute('CREATE INDEX evidence_target ON evidence_links(resource_type,resource_id)')
    op.execute('''CREATE TABLE milestone_completions(milestone_id uuid PRIMARY KEY REFERENCES milestones(id),
        evidence_id uuid NOT NULL REFERENCES evidence_links(evidence_id),completed_by uuid NOT NULL REFERENCES persons(id),
        actual_date date NOT NULL, reason varchar(1000), revision bigint NOT NULL, request_id uuid NOT NULL UNIQUE,created_at timestamptz NOT NULL DEFAULT now())''')
    op.execute('CREATE TRIGGER immutable_milestone_completion BEFORE UPDATE OR DELETE OR TRUNCATE ON milestone_completions FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c=op.get_bind(); c.exec_driver_sql('LOCK TABLE evidence_links,milestone_completions IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM evidence_links) OR EXISTS(SELECT FROM milestone_completions)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.drop_table('milestone_completions'); op.drop_table('evidence_links')
