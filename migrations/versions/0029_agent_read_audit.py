"""Preserve stable references with read and write audit receipts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0029'
down_revision = '0028'


def upgrade():
    op.add_column('audit_logs', sa.Column('source_refs', JSONB(none_as_null=True), nullable=True))
    op.create_check_constraint('ck_audit_source_refs_bounded_array', 'audit_logs',
        "CASE WHEN source_refs IS NULL THEN TRUE WHEN jsonb_typeof(source_refs) = 'array' "
        'THEN jsonb_array_length(source_refs) <= 100 ELSE FALSE END')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE audit_logs IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM audit_logs WHERE source_refs IS NOT NULL)')):
        raise RuntimeError('Refusing to discard written audit reference history')
    op.drop_constraint('ck_audit_source_refs_bounded_array', 'audit_logs', type_='check')
    op.drop_column('audit_logs', 'source_refs')
