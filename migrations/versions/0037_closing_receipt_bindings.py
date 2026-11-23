"""Constrain explicit project grants and risk receipts to their originating records."""
import sqlalchemy as sa
from alembic import op

revision = '0037'
down_revision = '0036'

ACCEPTANCES = 'risk_handoff_acceptances'
RESOLUTIONS = 'risk_resolutions'
ACCEPTANCE_KEY = ('id', 'risk_id', 'project_id', 'risk_revision', 'receiver_id', 'evidence_id')

KEYS = (
    ('uq_agent_credentials_id_org', 'agent_credentials', ('id', 'org_id')),
    ('uq_risks_id_project', 'risks', ('id', 'project_id')),
    ('uq_evidence_files_id_project', 'evidence_files', ('id', 'project_id')),
    ('uq_project_members_id_project_person', 'project_members', ('id', 'project_id', 'person_id')),
    ('uq_risk_acceptance_binding', ACCEPTANCES, ACCEPTANCE_KEY),
)

GRANT_KEYS = (
    ('fk_agent_grants_credential_org', 'agent_project_grants', 'agent_credentials',
     ('credential_id', 'org_id'), ('id', 'org_id')),
    ('fk_agent_grants_project_org', 'agent_project_grants', 'projects', ('org_id', 'project_id'), ('org_id', 'id')),
)

RECEIPT_KEYS = (
    ('fk_risk_acceptances_risk_project', ACCEPTANCES, 'risks', ('risk_id', 'project_id'), ('id', 'project_id')),
    ('fk_risk_acceptances_evidence_project', ACCEPTANCES, 'evidence_files', ('evidence_id', 'project_id'), ('id', 'project_id')),
    ('fk_risk_acceptances_reviewer', ACCEPTANCES, 'project_members',
     ('reviewer_assignment_id', 'project_id', 'actor_id'), ('id', 'project_id', 'person_id')),
    ('fk_risk_acceptances_request', ACCEPTANCES, ACCEPTANCES,
     ('request_receipt_id', 'risk_id', 'project_id', 'risk_revision', 'receiver_id', 'evidence_id'), ACCEPTANCE_KEY),
    ('fk_risk_resolutions_risk_project', RESOLUTIONS, 'risks', ('risk_id', 'project_id'), ('id', 'project_id')),
    ('fk_risk_resolutions_evidence_project', RESOLUTIONS, 'evidence_files', ('evidence_id', 'project_id'), ('id', 'project_id')),
    ('fk_risk_resolutions_reviewer', RESOLUTIONS, 'project_members',
     ('reviewer_assignment_id', 'project_id', 'actor_id'), ('id', 'project_id', 'person_id')),
    ('fk_risk_resolutions_acceptance', RESOLUTIONS, ACCEPTANCES,
     ('acceptance_id', 'risk_id', 'project_id', 'prior_revision', 'receiver_id', 'evidence_id'), ACCEPTANCE_KEY),
)

VALID_RISK_SOURCE = '''
    EXISTS (SELECT FROM evidence_links e WHERE e.evidence_id=source.eid AND e.resource_type='risk' AND e.resource_id=source.rid)
    AND (source.receiver IS NULL OR EXISTS (SELECT FROM projects p JOIN persons n ON n.org_id=p.org_id
         WHERE p.id=source.pid AND n.id=source.receiver))
    AND CASE source.action
      WHEN 'receiver_confirmed' THEN EXISTS (SELECT FROM risk_handoff_acceptances a
           WHERE a.id=source.prior AND a.kind='requested' AND a.scope=source.scope_text)
      WHEN 'transferred' THEN EXISTS (SELECT FROM risk_handoff_acceptances a
           WHERE a.id=source.prior AND a.kind IN ('receiver_confirmed','reviewer_verified'))
      ELSE source.prior IS NULL AND (source.action<>'closed' OR source.receiver IS NULL)
    END'''


def upgrade():
    c = op.get_bind()
    schema = c.dialect.identifier_preparer.quote_identifier(c.exec_driver_sql('SELECT current_schema()').scalar_one())
    c.exec_driver_sql('LOCK TABLE agent_project_grants, risk_handoff_acceptances, risk_resolutions IN ACCESS EXCLUSIVE MODE')
    for name, table, columns in KEYS:
        op.create_unique_constraint(name, table, columns)

    op.add_column('agent_project_grants', sa.Column('org_id', sa.Uuid))
    op.execute('''UPDATE agent_project_grants g SET org_id=c.org_id
        FROM agent_credentials c WHERE c.id=g.credential_id''')
    op.alter_column('agent_project_grants', 'org_id', nullable=False)
    for name, table, parent, columns, references in GRANT_KEYS + RECEIPT_KEYS:
        op.create_foreign_key(name, table, parent, columns, references)

    op.execute(f'''CREATE FUNCTION pmo_bind_agent_grant_org() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,{schema} AS $$
        BEGIN
          IF NEW.org_id IS NULL THEN
            SELECT org_id INTO NEW.org_id FROM agent_credentials WHERE id=NEW.credential_id;
          END IF;
          RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER agent_grant_org_binding BEFORE INSERT ON agent_project_grants
        FOR EACH ROW EXECUTE FUNCTION pmo_bind_agent_grant_org()''')

    # The compared revisions are from immutable receipts. A later live risk revision is allowed.
    invalid = c.exec_driver_sql(f'''
        WITH binding(pid,rid,eid,receiver,prior,action,scope_text) AS (
          SELECT project_id,risk_id,evidence_id,receiver_id,request_receipt_id,kind,scope FROM risk_handoff_acceptances
          UNION ALL
          SELECT project_id,risk_id,evidence_id,receiver_id,acceptance_id,kind,NULL FROM risk_resolutions
        ) SELECT EXISTS (SELECT FROM binding source WHERE NOT ({VALID_RISK_SOURCE}))''').scalar_one()
    if invalid:
        raise RuntimeError('Risk receipt migration stopped: a historical receipt has a mismatched source')

    op.execute(f'''CREATE FUNCTION pmo_bind_risk_receipt() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,{schema} AS $$
        DECLARE prior uuid; scope_text text; receipt_revision bigint; live_revision bigint; valid_source boolean;
        BEGIN
          IF TG_TABLE_NAME='risk_handoff_acceptances' THEN
            prior := NEW.request_receipt_id;
            scope_text := NEW.scope;
            receipt_revision := NEW.risk_revision;
          ELSE
            prior := NEW.acceptance_id;
            receipt_revision := NEW.revision;
          END IF;
          SELECT {VALID_RISK_SOURCE} INTO valid_source
          FROM (VALUES (NEW.project_id,NEW.risk_id,NEW.evidence_id,NEW.receiver_id,prior,NEW.kind,scope_text))
            AS source(pid,rid,eid,receiver,prior,action,scope_text);
          IF NOT valid_source THEN
            RAISE check_violation USING CONSTRAINT='ck_risk_receipt_source', MESSAGE='Risk receipt source does not match';
          END IF;
          SELECT revision INTO live_revision FROM risks WHERE id=NEW.risk_id FOR SHARE;
          IF live_revision IS NOT NULL AND live_revision <> receipt_revision THEN
            RAISE check_violation USING CONSTRAINT='ck_risk_receipt_version', MESSAGE='New risk receipt does not match the current version';
          END IF;
          RETURN NEW;
        END $$''')
    for table in (ACCEPTANCES, RESOLUTIONS):
        op.execute(f'''CREATE TRIGGER risk_receipt_binding BEFORE INSERT ON {table}
            FOR EACH ROW EXECUTE FUNCTION pmo_bind_risk_receipt()''')


def downgrade():
    for table in (RESOLUTIONS, ACCEPTANCES):
        op.execute(f'DROP TRIGGER risk_receipt_binding ON {table}')
    op.execute('DROP FUNCTION pmo_bind_risk_receipt()')
    op.execute('DROP TRIGGER agent_grant_org_binding ON agent_project_grants')
    op.execute('DROP FUNCTION pmo_bind_agent_grant_org()')
    for name, table, _, _, _ in reversed(GRANT_KEYS + RECEIPT_KEYS):
        op.drop_constraint(name, table, type_='foreignkey')
    op.drop_column('agent_project_grants', 'org_id')
    for name, table, _ in reversed(KEYS):
        op.drop_constraint(name, table, type_='unique')
