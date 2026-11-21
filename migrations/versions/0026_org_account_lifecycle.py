"""Organization invitations, versioned login status, and durable identity events."""
import sqlalchemy as sa
from alembic import op

revision = '0026'
down_revision = '0025'


def upgrade():
    op.execute('ALTER TABLE user_accounts ADD COLUMN status_revision bigint NOT NULL DEFAULT 1 CHECK(status_revision>=1)')
    op.execute('''CREATE TABLE organization_user_invitations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), org_id uuid NOT NULL REFERENCES organizations(id),
        email varchar(254) NOT NULL CHECK(email=lower(email)),
        role_hint varchar(32) NOT NULL CHECK(role_hint IN ('member','pm','reviewer','sponsor','client_representative')),
        issuer_id uuid NOT NULL REFERENCES user_accounts(id),
        code_hash char(64) NOT NULL UNIQUE CHECK(code_hash ~ '^[a-f0-9]{64}$'),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision IN (1,2)),
        issued_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
        accepted_at timestamptz, account_id uuid UNIQUE REFERENCES user_accounts(id),
        revoked_at timestamptz, revoked_by uuid REFERENCES user_accounts(id), revocation_reason varchar(500),
        CHECK(expires_at>issued_at),
        CHECK((accepted_at IS NOT NULL)=(account_id IS NOT NULL)),
        CHECK((revoked_at IS NOT NULL)=(revoked_by IS NOT NULL) AND (revoked_at IS NOT NULL)=(revocation_reason IS NOT NULL)),
        CHECK(NOT (accepted_at IS NOT NULL AND revoked_at IS NOT NULL)),
        CHECK((revision=2)=(accepted_at IS NOT NULL OR revoked_at IS NOT NULL))
    )''')
    op.execute('CREATE INDEX ix_org_invitations ON organization_user_invitations(org_id,issued_at DESC,id DESC)')
    op.execute('CREATE INDEX ix_org_invite_email ON organization_user_invitations(org_id,email,expires_at)')
    op.execute('CREATE UNIQUE INDEX ux_org_enrolled_email ON organization_user_invitations(org_id,email) WHERE account_id IS NOT NULL')
    op.execute('''CREATE FUNCTION pmo_preserve_org_invite() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF OLD.revision<>1 OR NEW.revision<>2 OR
                (NEW.id,NEW.org_id,NEW.email,NEW.role_hint,NEW.issuer_id,NEW.code_hash,NEW.issued_at,NEW.expires_at)
                IS DISTINCT FROM (OLD.id,OLD.org_id,OLD.email,OLD.role_hint,OLD.issuer_id,OLD.code_hash,OLD.issued_at,OLD.expires_at)
            THEN
                RAISE check_violation USING MESSAGE = 'Organization invitation history cannot be rewritten';
            END IF;
            RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER preserve_org_invite BEFORE UPDATE ON organization_user_invitations
        FOR EACH ROW EXECUTE FUNCTION pmo_preserve_org_invite()''')
    op.execute('''CREATE TABLE account_status_changes (
        account_id uuid NOT NULL REFERENCES user_accounts(id), revision bigint NOT NULL CHECK(revision>=2),
        changed_by uuid NOT NULL REFERENCES user_accounts(id), request_id uuid NOT NULL UNIQUE,
        occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        is_active boolean NOT NULL, reason varchar(500) NOT NULL,
        PRIMARY KEY(account_id,revision)
    )''')
    op.execute('''CREATE TABLE organization_event_inbox (
        dedupe_key varchar(160) PRIMARY KEY, event_id uuid NOT NULL UNIQUE REFERENCES outbox_events(id),
        org_id uuid NOT NULL REFERENCES organizations(id), event_type varchar(80) NOT NULL,
        received_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )''')
    for table, actions in [('organization_user_invitations', 'DELETE OR TRUNCATE'),
                           ('account_status_changes', 'UPDATE OR DELETE OR TRUNCATE'),
                           ('organization_event_inbox', 'UPDATE OR DELETE OR TRUNCATE')]:
        op.execute(f'CREATE TRIGGER immutable_{table} BEFORE {actions} ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE organization_user_invitations,account_status_changes,organization_event_inbox IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('''SELECT EXISTS(SELECT FROM organization_user_invitations) OR
            EXISTS(SELECT FROM account_status_changes) OR EXISTS(SELECT FROM organization_event_inbox)''')):
        raise RuntimeError('Refusing to discard account lifecycle history')
    op.drop_table('organization_event_inbox')
    op.drop_table('account_status_changes')
    op.drop_table('organization_user_invitations')
    op.execute('DROP FUNCTION pmo_preserve_org_invite()')
    op.execute('ALTER TABLE user_accounts DROP COLUMN status_revision')
