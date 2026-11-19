from alembic import op
import sqlalchemy as sa
from pmo.schema_v1 import metadata

revision = '0001'
down_revision = None

def upgrade():
    conn = op.get_bind()
    metadata.create_all(conn, checkfirst=False)
    op.execute("CREATE FUNCTION pmo_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only audit'; END $$")
    op.execute('CREATE TRIGGER pmo_audit_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_logs FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')

def downgrade():
    conn = op.get_bind()
    for table in metadata.sorted_tables:
        conn.execute(sa.text(f'LOCK TABLE "{table.name}" IN ACCESS EXCLUSIVE MODE'))
        if conn.scalar(sa.select(sa.literal(True)).select_from(table).limit(1)):
            raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    metadata.drop_all(conn, checkfirst=False)
    op.execute('DROP FUNCTION pmo_append_only()')
