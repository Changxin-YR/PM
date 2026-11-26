"""Bind official weekly attachments to authorized recipients and channel receipts."""
from alembic import op

revision = '0043'
down_revision = '0042'


def upgrade():
    op.execute('''CREATE TABLE weekly_delivery_authorizations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id uuid NOT NULL REFERENCES projects(id),
        recipient_key text NOT NULL CHECK (recipient_key ~ '^[a-z0-9][a-z0-9_.-]{1,79}$'),
        display_name text NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
        channel text NOT NULL CHECK (channel = 'synthetic_inbox'),
        approved_by uuid NOT NULL REFERENCES persons(id),
        approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        approval_hash char(64) NOT NULL CHECK (approval_hash ~ '^[a-f0-9]{64}$'),
        expires_at timestamptz NOT NULL CHECK (expires_at > approved_at),
        revoked_at timestamptz CHECK (revoked_at >= approved_at),
        UNIQUE (id, project_id)
    )''')
    op.execute('CREATE INDEX ix_weekly_delivery_authorizations_project ON weekly_delivery_authorizations(project_id)')
    op.execute('''CREATE TABLE weekly_deliveries (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id uuid NOT NULL REFERENCES projects(id),
        report_id uuid NOT NULL REFERENCES reports(id),
        report_version integer NOT NULL CHECK (report_version > 0),
        report_revision bigint NOT NULL CHECK (report_revision > 1),
        report_hash char(64) NOT NULL CHECK (report_hash ~ '^[a-f0-9]{64}$'),
        attachment_hash char(64) NOT NULL CHECK (attachment_hash ~ '^[a-f0-9]{64}$'),
        authorization_id uuid NOT NULL,
        authorization_hash char(64) NOT NULL CHECK (authorization_hash ~ '^[a-f0-9]{64}$'),
        recipient_key text NOT NULL,
        channel text NOT NULL CHECK (channel = 'synthetic_inbox'),
        requested_by uuid NOT NULL REFERENCES persons(id),
        queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        status text NOT NULL CHECK (status IN ('pending', 'submitted', 'delivered', 'blocked', 'unknown', 'failed')),
        provider_message_id uuid,
        channel_received_at timestamptz,
        error_code text,
        FOREIGN KEY (authorization_id, project_id) REFERENCES weekly_delivery_authorizations(id, project_id),
        UNIQUE (report_id, report_revision, recipient_key, channel),
        CHECK ((status='delivered') = (channel_received_at IS NOT NULL)),
        CHECK (status NOT IN ('submitted','delivered') OR provider_message_id IS NOT NULL)
    )''')
    op.execute('CREATE INDEX ix_weekly_deliveries_report ON weekly_deliveries(project_id,report_id,queued_at)')
    op.execute('''CREATE TABLE weekly_delivery_inbox (
        message_id uuid PRIMARY KEY REFERENCES weekly_deliveries(id),
        recipient_key text NOT NULL,
        attachment bytea NOT NULL CHECK (octet_length(attachment) BETWEEN 1 AND 65536),
        attachment_hash char(64) NOT NULL CHECK (attachment_hash ~ '^[a-f0-9]{64}$'),
        received_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )''')
    op.execute('''CREATE TRIGGER immutable_weekly_delivery_inbox BEFORE UPDATE OR DELETE OR TRUNCATE
        ON weekly_delivery_inbox FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE weekly_delivery_inbox,weekly_deliveries,weekly_delivery_authorizations IN ACCESS EXCLUSIVE MODE')
    if c.exec_driver_sql('''SELECT EXISTS(SELECT FROM weekly_delivery_inbox)
            OR EXISTS(SELECT FROM weekly_deliveries) OR EXISTS(SELECT FROM weekly_delivery_authorizations)''').scalar():
        raise RuntimeError('Refusing destructive downgrade of weekly report delivery evidence')
    op.drop_table('weekly_delivery_inbox')
    op.drop_table('weekly_deliveries')
    op.drop_table('weekly_delivery_authorizations')
