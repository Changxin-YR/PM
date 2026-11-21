"""Track immutable Fact sources and human verification history."""
from alembic import op
import sqlalchemy as sa

revision = '0024'
down_revision = '0023'


def upgrade():
    op.execute('''CREATE TABLE fact_source_receipts (
        fact_id uuid PRIMARY KEY REFERENCES facts(id),
        report_revision bigint NOT NULL CHECK(report_revision >= 1),
        content_hash char(64) NOT NULL CHECK(content_hash ~ '^[a-f0-9]{64}$'),
        quote_path text NOT NULL,
        quote_offset int NOT NULL CHECK(quote_offset >= 0),
        group_key char(64) NOT NULL CHECK(group_key ~ '^[a-f0-9]{64}$'),
        proposal_hash char(64) NOT NULL UNIQUE CHECK(proposal_hash ~ '^[a-f0-9]{64}$')
    )''')
    op.execute('CREATE INDEX ix_fact_source_group ON fact_source_receipts(group_key, fact_id)')
    op.execute('CREATE INDEX ix_facts_report ON facts(report_id, created_at, id)')
    op.execute('CREATE INDEX ix_fact_clarification_status ON fact_clarifications(fact_id, status)')
    op.execute('''CREATE TABLE fact_history (
        fact_id uuid NOT NULL REFERENCES fact_source_receipts(fact_id),
        revision bigint NOT NULL CHECK(revision >= 1),
        action varchar(20) NOT NULL CHECK(action IN ('proposed','verified','disputed','resolved','superseded')),
        actor_id uuid NOT NULL REFERENCES persons(id),
        request_id uuid NOT NULL,
        occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        validation_status varchar(20) NOT NULL CHECK(validation_status IN ('candidate','verified','disputed','rejected')),
        trust varchar(8) NOT NULL CHECK(trust IN ('low','medium','high')),
        reason text NOT NULL,
        evidence_ref varchar(256),
        evidence_hash char(64) CHECK(evidence_hash ~ '^[a-f0-9]{64}$'),
        PRIMARY KEY(fact_id, revision)
    )''')
    for table in ('fact_source_receipts', 'fact_history'):
        op.execute(f'''CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()''')
    op.execute('''CREATE FUNCTION pmo_preserve_fact_source() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF (NEW.id,NEW.project_id,NEW.report_id,NEW.created_by,NEW.created_at,NEW.subject,NEW.predicate,
                NEW.as_of,NEW.value,NEW.quote,NEW.source_ref) IS DISTINCT FROM
               (OLD.id,OLD.project_id,OLD.report_id,OLD.created_by,OLD.created_at,OLD.subject,OLD.predicate,
                OLD.as_of,OLD.value,OLD.quote,OLD.source_ref) THEN
                RAISE check_violation USING MESSAGE = 'Fact source cannot be overwritten';
            END IF;
            RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER preserve_fact_source BEFORE UPDATE ON facts
        FOR EACH ROW EXECUTE FUNCTION pmo_preserve_fact_source()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE facts,fact_source_receipts,fact_history,fact_clarifications IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM fact_source_receipts)')):
        raise RuntimeError('Refusing to discard Fact source and verification history')
    op.execute('DROP TRIGGER preserve_fact_source ON facts')
    op.execute('DROP FUNCTION pmo_preserve_fact_source()')
    op.drop_table('fact_history')
    op.drop_table('fact_source_receipts')
    op.drop_index('ix_fact_clarification_status', table_name='fact_clarifications')
    op.drop_index('ix_facts_report', table_name='facts')
