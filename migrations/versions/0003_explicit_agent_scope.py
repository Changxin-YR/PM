"""Separate affirmative project grants from automatic creator-draft scope."""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'


def upgrade():
    op.create_table('agent_project_grants',
        sa.Column('credential_id', sa.Uuid, sa.ForeignKey('agent_credentials.id'), primary_key=True),
        sa.Column('project_id', sa.Uuid, sa.ForeignKey('projects.id'), primary_key=True),
        sa.Column('granted_by', sa.Uuid, sa.ForeignKey('user_accounts.id'), nullable=False),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql('LOCK TABLE agent_project_grants IN ACCESS EXCLUSIVE MODE')
    if connection.scalar(sa.text('SELECT EXISTS (SELECT 1 FROM agent_project_grants)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.drop_table('agent_project_grants')
