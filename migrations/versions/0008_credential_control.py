"""Version the credential collection and preserve administrative decisions."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'


def upgrade():
    op.execute('LOCK TABLE agent_credentials IN SHARE ROW EXCLUSIVE MODE')
    op.execute('''CREATE TABLE agent_collection_versions(account_id uuid PRIMARY KEY REFERENCES user_accounts(id),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1))''')
    op.execute('''CREATE TABLE agent_token_versions(token_id uuid PRIMARY KEY REFERENCES agent_credentials(id),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1))''')
    op.execute('INSERT INTO agent_token_versions(token_id) SELECT id FROM agent_credentials')
    op.execute('''CREATE TABLE agent_security_events(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        token_id uuid NOT NULL REFERENCES agent_credentials(id), actor_id uuid NOT NULL REFERENCES user_accounts(id),
        action varchar(16) NOT NULL CHECK(action IN ('issue','revoke')), reason varchar(500),
        request_id uuid NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(token_id,action))''')
    op.execute('CREATE TRIGGER immutable_agent_security_events BEFORE UPDATE OR DELETE OR TRUNCATE ON agent_security_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    op.execute('''CREATE FUNCTION version_agent_scope() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        INSERT INTO agent_token_versions(token_id) VALUES(NEW.id) ON CONFLICT(token_id) DO UPDATE SET revision=agent_token_versions.revision+1;
        INSERT INTO agent_collection_versions(account_id,revision) VALUES(NEW.delegated_by,2) ON CONFLICT(account_id) DO UPDATE SET revision=agent_collection_versions.revision+1;
        RETURN NEW; END $$''')
    op.execute('''CREATE TRIGGER version_credential AFTER INSERT OR UPDATE OF allowed_actions,allowed_project_ids,
        allow_project_create,expires_at,status,revoked_at ON agent_credentials FOR EACH ROW EXECUTE FUNCTION version_agent_scope()''')
    op.execute("UPDATE idempotency_keys SET expires_at=created_at+interval '90 days' WHERE expires_at<created_at+interval '90 days'")


def downgrade():
    c=op.get_bind()
    c.exec_driver_sql('LOCK TABLE agent_credentials,agent_security_events IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM agent_security_events) OR EXISTS(SELECT FROM agent_collection_versions)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.execute('DROP TRIGGER version_credential ON agent_credentials')
    op.execute('DROP FUNCTION version_agent_scope()')
    for table in ('agent_security_events','agent_token_versions','agent_collection_versions'):
        op.drop_table(table)
