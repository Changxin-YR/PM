import sqlalchemy as sa
from alembic import op
from pmo.session_schema import metadata

revision='0002'
down_revision='0001'

def upgrade():
    metadata.create_all(op.get_bind(),checkfirst=False)
    op.create_foreign_key('fk_session_account','web_sessions','user_accounts',['account_id'],['id'])

def downgrade():
    conn=op.get_bind()
    for table in metadata.sorted_tables:
        conn.execute(sa.text(f'LOCK TABLE "{table.name}" IN ACCESS EXCLUSIVE MODE'))
        if conn.scalar(sa.select(sa.literal(True)).select_from(table).limit(1)):
            raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    metadata.drop_all(conn,checkfirst=False)
