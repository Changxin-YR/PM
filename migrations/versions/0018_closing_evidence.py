"""Append-only reconciliation and independently accepted risk receipts."""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'

TABLES = ('cost_reconciliation_grants', 'cost_reconciliations', 'risk_handoff_acceptances', 'risk_resolutions')
EVIDENCE = "'charter','milestone','requirement','review','risk','report','cost_posting','decision'"
RISK_RECEIPT = """id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id), risk_id uuid NOT NULL REFERENCES risks(id),
    evidence_id uuid NOT NULL REFERENCES evidence_links(evidence_id),
    actor_id uuid NOT NULL REFERENCES persons(id), reviewer_assignment_id uuid REFERENCES project_members(id),
    receiver_id uuid REFERENCES persons(id), request_id uuid NOT NULL UNIQUE,
    evidence_sha256 char(64) NOT NULL CHECK(evidence_sha256 ~ '^[a-f0-9]{64}$'),
    evidence_revision bigint NOT NULL CHECK(evidence_revision>=2),
    fingerprint char(64) NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()"""


def upgrade():
    op.drop_constraint('evidence_links_resource_type_check', 'evidence_links', type_='check')
    op.create_check_constraint('evidence_links_resource_type_check', 'evidence_links', f"resource_type IN ({EVIDENCE},'project')")
    op.execute('''CREATE TABLE cost_reconciliation_grants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL REFERENCES projects(id),
        person_id uuid NOT NULL REFERENCES persons(id), granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz, revoked_at timestamptz,
        CHECK(expires_at IS NULL OR expires_at>granted_at), CHECK(revoked_at IS NULL OR revoked_at>=granted_at)
    )''')
    op.execute('CREATE INDEX ix_cost_reconciliation_grants_scope ON cost_reconciliation_grants(project_id,person_id) WHERE revoked_at IS NULL')
    op.execute('''CREATE TABLE cost_reconciliations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL REFERENCES projects(id),
        revision bigint NOT NULL CHECK(revision>=2), signed_by uuid NOT NULL REFERENCES persons(id),
        grant_id uuid NOT NULL REFERENCES cost_reconciliation_grants(id), signed_at timestamptz NOT NULL,
        covered_through timestamptz NOT NULL CHECK(covered_through<=signed_at), rule_version text NOT NULL,
        cost_hash char(64) NOT NULL CHECK(cost_hash ~ '^[a-f0-9]{64}$'),
        receipt_hash char(64) NOT NULL CHECK(receipt_hash ~ '^[a-f0-9]{64}$'),
        basis jsonb NOT NULL CHECK(jsonb_typeof(basis)='object'),
        lines jsonb NOT NULL CHECK(jsonb_typeof(lines)='array' AND jsonb_array_length(lines)=2),
        request_id uuid NOT NULL UNIQUE, UNIQUE(project_id,revision)
    )''')
    op.execute(f'''CREATE TABLE risk_handoff_acceptances ({RISK_RECEIPT},
        kind text NOT NULL CHECK(kind IN ('requested','receiver_confirmed','reviewer_verified')),
        risk_revision bigint NOT NULL CHECK(risk_revision>=1),
        risk_fingerprint char(64) NOT NULL CHECK(risk_fingerprint ~ '^[a-f0-9]{{64}}$'),
        scope text NOT NULL CHECK(length(btrim(scope))>=5),
        request_receipt_id uuid REFERENCES risk_handoff_acceptances(id), verification jsonb,
        CHECK(receiver_id IS NOT NULL),
        CHECK((kind='receiver_confirmed')=(request_receipt_id IS NOT NULL)),
        CHECK((kind='reviewer_verified')=(verification IS NOT NULL)),
        CHECK(kind<>'receiver_confirmed' OR actor_id=receiver_id),
        UNIQUE(risk_id,risk_revision,receiver_id,evidence_id,kind)
    )''')
    op.execute(f'''CREATE TABLE risk_resolutions ({RISK_RECEIPT},
        kind text NOT NULL CHECK(kind IN ('closed','transferred')),
        prior_revision bigint NOT NULL CHECK(prior_revision>=1), revision bigint NOT NULL CHECK(revision=prior_revision+1),
        acceptance_id uuid REFERENCES risk_handoff_acceptances(id),
        CHECK(reviewer_assignment_id IS NOT NULL),
        CHECK((kind='transferred')=(receiver_id IS NOT NULL AND acceptance_id IS NOT NULL)),
        UNIQUE(risk_id,revision)
    )''')
    for table in TABLES[1:]:
        op.execute(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE ' + ','.join((*TABLES, 'evidence_links')) + ' IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT ' + ' OR '.join(f'EXISTS(SELECT FROM {table})' for table in TABLES)
                        + " OR EXISTS(SELECT FROM evidence_links WHERE resource_type='project')")):
        raise RuntimeError('Refusing destructive downgrade of populated closing evidence')
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_constraint('evidence_links_resource_type_check', 'evidence_links', type_='check')
    op.create_check_constraint('evidence_links_resource_type_check', 'evidence_links', f'resource_type IN ({EVIDENCE})')
