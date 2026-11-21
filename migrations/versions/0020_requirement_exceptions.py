"""Keep customer acceptance and revocation as immutable, ordered evidence."""
import sqlalchemy as sa
from alembic import op

revision = '0020'
down_revision = '0019'


def upgrade():
    op.execute('''CREATE TABLE requirement_exception_events (
        id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
        revision bigint NOT NULL CHECK(revision >= 2),
        kind text NOT NULL, actor_id uuid NOT NULL REFERENCES persons(id),
        assignment_id uuid NOT NULL REFERENCES project_members(id),
        evidence_id uuid REFERENCES evidence_files(id), revokes_id uuid UNIQUE,
        request_id uuid NOT NULL UNIQUE, created_at timestamptz NOT NULL,
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload) = 'object'),
        fingerprint char(64) NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
        UNIQUE(project_id, revision), UNIQUE(project_id, id),
        FOREIGN KEY(project_id, revokes_id) REFERENCES requirement_exception_events(project_id, id),
        CHECK((kind = 'signed' AND evidence_id IS NOT NULL AND revokes_id IS NULL)
           OR (kind = 'revoked' AND evidence_id IS NULL AND revokes_id IS NOT NULL AND revokes_id <> id))
    )''')
    op.execute('''CREATE TRIGGER immutable_requirement_exception_events
        BEFORE UPDATE OR DELETE OR TRUNCATE ON requirement_exception_events
        FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE requirement_exception_events IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM requirement_exception_events)')):
        raise RuntimeError('Refusing to discard written acceptance history')
    op.drop_table('requirement_exception_events')
