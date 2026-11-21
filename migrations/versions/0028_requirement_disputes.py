"""Store formal human objections to a signed requirement version."""
import sqlalchemy as sa
from alembic import op

revision = '0028'
down_revision = '0027'


def upgrade():
    op.execute('''CREATE TABLE requirement_disputes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id uuid NOT NULL REFERENCES projects(id),
        requirement_id uuid NOT NULL REFERENCES requirements(id),
        baseline_version int NOT NULL CHECK (baseline_version > 0),
        requirement_revision bigint NOT NULL CHECK (requirement_revision > 0),
        customer_id uuid NOT NULL REFERENCES persons(id),
        assignment_id uuid NOT NULL REFERENCES project_members(id),
        stakeholder_id uuid NOT NULL REFERENCES stakeholders(id),
        reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 4 AND 2000),
        opened_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        open_request_id uuid NOT NULL UNIQUE,
        revision bigint NOT NULL DEFAULT 1,
        withdrawn_at timestamptz,
        withdrawal_reason text,
        withdrawal_request_id uuid UNIQUE,
        CHECK ((revision=1 AND withdrawn_at IS NULL AND withdrawal_reason IS NULL AND withdrawal_request_id IS NULL)
            OR (revision=2 AND withdrawn_at IS NOT NULL AND withdrawn_at>=opened_at
                AND withdrawal_reason IS NOT NULL AND length(btrim(withdrawal_reason)) BETWEEN 4 AND 2000
                AND withdrawal_request_id IS NOT NULL AND withdrawal_request_id<>open_request_id))
    )''')
    op.execute('''CREATE UNIQUE INDEX uq_requirement_dispute_open ON requirement_disputes
        (project_id, requirement_id, baseline_version, customer_id) WHERE withdrawn_at IS NULL''')
    op.execute('''CREATE INDEX ix_requirement_dispute_history ON requirement_disputes
        (project_id, requirement_id, opened_at DESC, id DESC)''')
    op.execute('''CREATE FUNCTION pmo_preserve_requirement_dispute() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $$
        BEGIN
            IF TG_OP <> 'UPDATE' THEN
                RAISE check_violation USING MESSAGE = 'Requirement dispute history cannot be discarded';
            END IF;
            IF OLD.revision <> 1 OR NEW.revision <> 2 OR
                (to_jsonb(NEW) - ARRAY['revision','withdrawn_at','withdrawal_reason','withdrawal_request_id']) IS DISTINCT FROM
                (to_jsonb(OLD) - ARRAY['revision','withdrawn_at','withdrawal_reason','withdrawal_request_id']) THEN
                RAISE check_violation USING MESSAGE = 'Only one withdrawal of the original statement is allowed';
            END IF;
            RETURN NEW;
        END $$''')
    op.execute('''CREATE TRIGGER preserve_requirement_dispute BEFORE UPDATE OR DELETE ON requirement_disputes
        FOR EACH ROW EXECUTE FUNCTION pmo_preserve_requirement_dispute()''')
    op.execute('''CREATE TRIGGER preserve_requirement_dispute_truncate BEFORE TRUNCATE ON requirement_disputes
        FOR EACH STATEMENT EXECUTE FUNCTION pmo_preserve_requirement_dispute()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE requirement_disputes IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS (SELECT FROM requirement_disputes)')):
        raise RuntimeError('Refusing to discard requirement dispute history')
    op.drop_table('requirement_disputes')
    op.execute('DROP FUNCTION pmo_preserve_requirement_dispute()')
