"""Immutable per-step requirement delivery and sign-off records."""
from alembic import op
import sqlalchemy as sa

revision='0014'
down_revision='0013'


def upgrade():
    op.execute('''CREATE TABLE requirement_transitions(
        requirement_id uuid NOT NULL REFERENCES requirements(id), revision bigint NOT NULL,
        baseline_version integer NOT NULL CHECK(baseline_version>0),
        status text NOT NULL CHECK(status IN ('delivered','accepted','customer_confirmed')),
        evidence_id uuid NOT NULL REFERENCES evidence_links(evidence_id),actor_id uuid NOT NULL REFERENCES persons(id),
        actor_type text NOT NULL CHECK(actor_type='human' OR (actor_type='agent' AND status='delivered')),
        request_id uuid UNIQUE NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(requirement_id,revision))''')
    op.execute('CREATE TRIGGER immutable_requirement_transitions BEFORE UPDATE OR DELETE OR TRUNCATE ON requirement_transitions FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c=op.get_bind(); c.exec_driver_sql('LOCK TABLE requirement_transitions IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM requirement_transitions)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.drop_table('requirement_transitions')
