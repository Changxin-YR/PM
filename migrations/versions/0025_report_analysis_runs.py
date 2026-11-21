"""Keep resumable report-analysis runs separate from authoritative Facts."""
from alembic import op
import sqlalchemy as sa

revision = '0025'
down_revision = '0024'


def upgrade():
    op.execute('ALTER TABLE web_sessions ADD COLUMN session_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE')
    op.execute('''CREATE TABLE report_analysis_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        report_id uuid NOT NULL REFERENCES reports(id),
        project_id uuid NOT NULL REFERENCES projects(id),
        requested_by uuid NOT NULL REFERENCES persons(id),
        requested_account_id uuid NOT NULL REFERENCES user_accounts(id),
        requested_session_id uuid NOT NULL REFERENCES web_sessions(session_id),
        request_id uuid NOT NULL UNIQUE,
        report_revision bigint NOT NULL CHECK(report_revision>=1),
        report_hash char(64) NOT NULL CHECK(report_hash ~ '^[a-f0-9]{64}$'),
        requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        status varchar(16) NOT NULL CHECK(status IN ('queued','running','ready','manual','stale')),
        attempts smallint NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 2),
        lease_id uuid, leased_until timestamptz,
        completed_at timestamptz, error_code varchar(64),
        candidates jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(candidates)='array' AND jsonb_array_length(candidates)<=20),
        fallback_task_id uuid REFERENCES tasks(id),
        CHECK((status='running') = (lease_id IS NOT NULL AND leased_until IS NOT NULL)),
        CHECK((status IN ('ready','manual','stale')) = (completed_at IS NOT NULL)),
        CHECK((status IN ('manual','stale')) = (error_code IS NOT NULL)),
        CHECK(status='ready' OR candidates='[]'::jsonb),
        CHECK(status='manual' OR fallback_task_id IS NULL)
    )''')
    op.execute('CREATE UNIQUE INDEX ux_report_analysis_active ON report_analysis_runs(report_id) WHERE status IN (\'queued\',\'running\')')
    op.execute('CREATE INDEX ix_report_analysis_queue ON report_analysis_runs(requested_at,id) WHERE status IN (\'queued\',\'running\')')
    op.execute('CREATE INDEX ix_report_analysis_report ON report_analysis_runs(report_id,requested_at DESC,id DESC)')
    op.execute('''CREATE UNIQUE INDEX ux_report_analysis_manual_task ON tasks(project_id,source_id)
        WHERE source_type='report_analysis' AND status IN ('open','todo','in_progress','blocked')''')
    op.execute('''CREATE FUNCTION pmo_preserve_analysis_source() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF OLD.status IN ('ready','manual','stale') OR
                (NEW.id,NEW.project_id,NEW.report_id,NEW.requested_by,NEW.requested_account_id,NEW.requested_session_id,
                    NEW.request_id,NEW.report_revision,NEW.report_hash,NEW.requested_at)
                IS DISTINCT FROM
                (OLD.id,OLD.project_id,OLD.report_id,OLD.requested_by,OLD.requested_account_id,OLD.requested_session_id,
                    OLD.request_id,OLD.report_revision,OLD.report_hash,OLD.requested_at) THEN
                RAISE check_violation USING MESSAGE = 'Report analysis receipts cannot be rewritten';
            END IF;
            IF NEW.attempts <> OLD.attempts + (CASE WHEN NEW.status='running' AND NEW.lease_id IS DISTINCT FROM OLD.lease_id
                    AND OLD.attempts<2 THEN 1 ELSE 0 END)
               OR (NEW.status='running' AND OLD.status='running' AND OLD.leased_until > clock_timestamp()) THEN
                RAISE check_violation USING MESSAGE = 'Invalid report analysis lease change';
            END IF;
            RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER preserve_analysis_source BEFORE UPDATE ON report_analysis_runs
        FOR EACH ROW EXECUTE FUNCTION pmo_preserve_analysis_source()''')
    op.execute('''CREATE TRIGGER immutable_analysis_delete BEFORE DELETE OR TRUNCATE ON report_analysis_runs
        FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE report_analysis_runs IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM report_analysis_runs)')):
        raise RuntimeError('Refusing to discard report analysis runs')
    op.drop_table('report_analysis_runs')
    op.drop_index('ux_report_analysis_manual_task', table_name='tasks')
    op.execute('ALTER TABLE web_sessions DROP COLUMN session_id')
    op.execute('DROP FUNCTION pmo_preserve_analysis_source()')
