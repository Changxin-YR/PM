"""Add verifiable report submissions without relabeling legacy versions."""
import sqlalchemy as sa
from alembic import op

revision = '0021'
down_revision = '0020'


def upgrade():
    op.execute('''ALTER TABLE reports
        ADD COLUMN content_hash char(64),
        ADD COLUMN submitted_by uuid REFERENCES persons(id),
        ADD COLUMN confirmed_by uuid REFERENCES persons(id),
        ADD COLUMN confirmed_at timestamptz,
        ADD CONSTRAINT ck_report_receipt CHECK (
            (content_hash IS NULL AND submitted_by IS NULL AND confirmed_by IS NULL AND confirmed_at IS NULL)
            OR (content_hash IS NOT NULL AND content_hash ~ '^[a-f0-9]{64}$' AND (
                (status='draft' AND submitted_by IS NULL AND submitted_at IS NULL
                    AND confirmed_by IS NULL AND confirmed_at IS NULL AND NOT is_current_confirmed)
                OR (status='submitted' AND submitted_by IS NOT NULL AND submitted_at IS NOT NULL
                    AND confirmed_by IS NULL AND confirmed_at IS NULL AND NOT is_current_confirmed)
                OR (status='confirmed' AND submitted_by IS NOT NULL AND submitted_at IS NOT NULL
                    AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND confirmed_at >= submitted_at))))
    ''')
    op.execute('''CREATE UNIQUE INDEX uq_reports_receipt_version ON reports(project_id,type,period,version_no)
        WHERE content_hash IS NOT NULL''')
    op.execute('''CREATE UNIQUE INDEX uq_reports_receipt_current ON reports(project_id,type,period)
        WHERE content_hash IS NOT NULL AND is_current''')
    op.execute('''CREATE INDEX ix_reports_weekly_submissions ON reports(project_id,period_start,submitted_at)
        WHERE type='weekly' AND submitted_by IS NOT NULL AND content_hash IS NOT NULL AND status IN ('submitted','confirmed')''')
    # Previous database-role provisioning made reports read-only. The API now edits named columns.
    op.execute('DROP TRIGGER IF EXISTS pmo_lock_only_guard ON reports')
    op.execute('''CREATE FUNCTION pmo_preserve_submitted_report() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF OLD.content_hash IS NOT NULL AND OLD.status IN ('submitted','confirmed') AND (
                (NEW.content_hash, NEW.structured_fields, NEW.narrative, NEW.submitted_by, NEW.submitted_at)
                    IS DISTINCT FROM (OLD.content_hash, OLD.structured_fields, OLD.narrative, OLD.submitted_by, OLD.submitted_at)
                OR NEW.status NOT IN ('submitted','confirmed')
                OR (OLD.status='confirmed' AND (NEW.status, NEW.confirmed_by, NEW.confirmed_at)
                    IS DISTINCT FROM (OLD.status, OLD.confirmed_by, OLD.confirmed_at))) THEN
                RAISE check_violation USING MESSAGE = 'Submitted report history is immutable';
            END IF;
            RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER preserve_submitted_report BEFORE UPDATE ON reports
        FOR EACH ROW EXECUTE FUNCTION pmo_preserve_submitted_report()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE reports IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM reports WHERE content_hash IS NOT NULL)')):
        raise RuntimeError('Refusing to discard report receipts')
    op.execute('DROP TRIGGER preserve_submitted_report ON reports')
    op.execute('DROP FUNCTION pmo_preserve_submitted_report()')
    for index in ('ix_reports_weekly_submissions', 'uq_reports_receipt_current', 'uq_reports_receipt_version'):
        op.drop_index(index, table_name='reports')
    op.drop_constraint('ck_report_receipt', 'reports', type_='check')
    for column in ('confirmed_at', 'confirmed_by', 'submitted_by', 'content_hash'):
        op.drop_column('reports', column)
