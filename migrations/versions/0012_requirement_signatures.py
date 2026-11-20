"""Immutable initial requirement baseline attestations."""
from alembic import op
import sqlalchemy as sa

revision='0012'
down_revision='0011'


def upgrade():
    op.execute('''CREATE TABLE requirement_baseline_signatures (
        project_id uuid NOT NULL REFERENCES projects(id), revision bigint NOT NULL CHECK(revision>=2),
        signer_id uuid NOT NULL REFERENCES persons(id), signer_role text NOT NULL CHECK(signer_role IN ('pm','client_representative')),
        content_hash char(64) NOT NULL, snapshot jsonb NOT NULL, rationale text NOT NULL CHECK(length(trim(rationale))>=4),
        request_id uuid NOT NULL UNIQUE, signed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(project_id,revision))''')
    op.execute('''CREATE UNIQUE INDEX one_initial_requirement_baseline ON requirement_baseline_signatures(project_id)
        WHERE signer_role='client_representative' ''')
    op.execute('CREATE TRIGGER immutable_requirement_baseline BEFORE UPDATE OR DELETE OR TRUNCATE ON requirement_baseline_signatures FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c=op.get_bind(); c.exec_driver_sql('LOCK TABLE requirement_baseline_signatures IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM requirement_baseline_signatures)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.drop_table('requirement_baseline_signatures')
