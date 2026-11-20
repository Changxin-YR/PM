"""Keep every committed charter revision, including pre-migration records."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'


def upgrade():
    op.execute('LOCK TABLE charters IN SHARE ROW EXCLUSIVE MODE')
    op.execute('''CREATE TABLE charter_versions (
        charter_id uuid NOT NULL REFERENCES charters(id), revision bigint NOT NULL CHECK (revision >= 1),
        snapshot jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (charter_id, revision))''')
    op.execute('INSERT INTO charter_versions(charter_id,revision,snapshot) SELECT id,revision,to_jsonb(c) FROM charters c')
    op.execute('''CREATE FUNCTION pmo_version_charter() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND (NEW.revision <> OLD.revision + 1 OR NEW.project_id <> OLD.project_id OR NEW.id <> OLD.id) THEN
            RAISE EXCEPTION 'invalid charter revision';
          END IF;
          INSERT INTO charter_versions(charter_id,revision,snapshot) VALUES(NEW.id,NEW.revision,to_jsonb(NEW));
          RETURN NEW;
        END $$''')
    op.execute('CREATE TRIGGER pmo_record_charter AFTER INSERT OR UPDATE ON charters FOR EACH ROW EXECUTE FUNCTION pmo_version_charter()')
    op.execute('CREATE TRIGGER pmo_immutable_charter_version BEFORE UPDATE OR DELETE OR TRUNCATE ON charter_versions FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c=op.get_bind()
    c.exec_driver_sql('LOCK TABLE charters, charter_versions IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM charter_versions)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.execute('DROP TRIGGER pmo_record_charter ON charters')
    op.execute('DROP FUNCTION pmo_version_charter()')
    op.drop_table('charter_versions')
