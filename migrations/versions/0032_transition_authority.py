"""Record explicit, tenant-bound authority for individual lifecycle edges."""
import sqlalchemy as sa
from alembic import op

revision = '0032'
down_revision = '0031'


def upgrade():
    op.create_unique_constraint('uq_projects_org_id_id', 'projects', ('org_id', 'id'))
    op.create_unique_constraint('uq_persons_org_id_id', 'persons', ('org_id', 'id'))
    op.execute('''CREATE TABLE project_transition_grants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL, project_id uuid NOT NULL, person_id uuid NOT NULL,
        from_stage text NOT NULL, to_stage text NOT NULL,
        source_ref varchar(160) NOT NULL, valid_from timestamptz NOT NULL, expires_at timestamptz NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        revoked_at timestamptz, revocation_ref varchar(160),
        CONSTRAINT fk_transition_grants_project_org FOREIGN KEY (org_id, project_id) REFERENCES projects(org_id, id),
        CONSTRAINT fk_transition_grants_person_org FOREIGN KEY (org_id, person_id) REFERENCES persons(org_id, id),
        CONSTRAINT ck_transition_grants_edge CHECK ((from_stage, to_stage) IN (
            ('executing','on_hold'), ('on_hold','executing'), ('executing','terminated'), ('on_hold','terminated'),
            ('rejected','terminated'), ('feasibility_review','terminated'), ('closing_review','terminated'))),
        CONSTRAINT ck_transition_grants_validity CHECK (isfinite(valid_from) AND isfinite(expires_at) AND expires_at > valid_from),
        CONSTRAINT ck_transition_grants_source CHECK (source_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:/@-]*$'),
        CONSTRAINT ck_transition_grants_revocation CHECK (
            (revoked_at IS NULL AND revocation_ref IS NULL) OR
            (revoked_at IS NOT NULL AND isfinite(revoked_at) AND revoked_at >= recorded_at AND revocation_ref IS NOT NULL
                AND revocation_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:/@-]*$'))
    )''')
    op.execute('''CREATE INDEX ix_transition_grants_scope ON project_transition_grants
        (org_id, project_id, person_id, from_stage, to_stage, expires_at DESC) WHERE revoked_at IS NULL''')
    op.execute('''CREATE FUNCTION pmo_transition_grant_revocation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL OR
               to_jsonb(NEW) - ARRAY['revoked_at', 'revocation_ref'] IS DISTINCT FROM
               to_jsonb(OLD) - ARRAY['revoked_at', 'revocation_ref'] THEN
                RAISE EXCEPTION 'Transition grant history allows only one revocation' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER transition_grant_revocation BEFORE UPDATE ON project_transition_grants
        FOR EACH ROW EXECUTE FUNCTION pmo_transition_grant_revocation()''')
    op.execute('''CREATE TRIGGER immutable_transition_grants BEFORE DELETE OR TRUNCATE ON project_transition_grants
        FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE project_transition_grants IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS (SELECT FROM project_transition_grants)')):
        raise RuntimeError('Refusing destructive downgrade of transition authority history')
    op.drop_table('project_transition_grants')
    op.execute('DROP FUNCTION pmo_transition_grant_revocation()')
    op.drop_constraint('uq_persons_org_id_id', 'persons', type_='unique')
    op.drop_constraint('uq_projects_org_id_id', 'projects', type_='unique')
