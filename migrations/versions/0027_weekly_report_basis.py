"""Attach immutable source receipts to generated weekly reports."""
import sqlalchemy as sa
from alembic import op

revision = '0027'
down_revision = '0026'


def upgrade():
    op.execute('''ALTER TABLE reports ADD COLUMN weekly_basis jsonb,
        ADD CONSTRAINT ck_weekly_report_basis CHECK (weekly_basis IS NULL OR
            (type='weekly' AND content_hash IS NOT NULL AND jsonb_typeof(weekly_basis)='object'
                AND weekly_basis ? 'sourceHash' AND weekly_basis->>'sourceHash' IS NOT NULL
                AND weekly_basis->>'sourceHash' ~ '^[a-f0-9]{64}$'))''')
    op.execute('''CREATE FUNCTION pmo_preserve_weekly_basis() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $$
        BEGIN
            IF NEW.weekly_basis IS DISTINCT FROM OLD.weekly_basis THEN
                RAISE check_violation USING MESSAGE = 'Weekly report source receipts cannot be rewritten';
            END IF;
            RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER preserve_weekly_basis BEFORE UPDATE OF weekly_basis ON reports
        FOR EACH ROW EXECUTE FUNCTION pmo_preserve_weekly_basis()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE reports IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM reports WHERE weekly_basis IS NOT NULL)')):
        raise RuntimeError('Refusing to discard weekly report source receipts')
    op.execute('DROP TRIGGER preserve_weekly_basis ON reports')
    op.execute('DROP FUNCTION pmo_preserve_weekly_basis()')
    op.drop_constraint('ck_weekly_report_basis', 'reports', type_='check')
    op.drop_column('reports', 'weekly_basis')
