"""Allow terminating two review stages via independent lifecycle approval."""
import sqlalchemy as sa
from alembic import op

revision = '0022'
down_revision = '0021'

REVIEW_EDGES = """('drafting','charter_review'),('designing','feasibility_review'),('delivering','closing_review'),
    ('charter_review','designing'),('charter_review','drafting'),('charter_review','rejected'),
    ('feasibility_review','executing'),('feasibility_review','designing'),
    ('closing_review','closed'),('closing_review','executing'),('closing_review','delivering')"""
NON_GATE = """('rejected','drafting'),('rejected','terminated'),('executing','on_hold'),('on_hold','executing'),
    ('executing','delivering'),('delivering','executing'),('executing','terminated'),('on_hold','terminated'),
    ('closed','retrospective'),('terminated','retrospective'),('retrospective','archived')"""
TERMINATION = "('feasibility_review','terminated'),('closing_review','terminated')"


def check_edges(allowed):
    op.create_check_constraint('project_transition_intents_check', 'project_transition_intents', f'(from_stage,to_stage) IN ({allowed})')
    op.create_check_constraint('ck_project_stage_edges', 'project_stage_transitions',
        f'''(review_id IS NOT NULL AND approval_request_id IS NULL AND (from_stage,to_stage) IN ({REVIEW_EDGES}))
        OR (review_id IS NULL AND approval_request_id IS NOT NULL AND actor_type='human' AND (from_stage,to_stage) IN ({allowed}))''')


def drop_edges():
    op.drop_constraint('project_transition_intents_check', 'project_transition_intents', type_='check')
    op.drop_constraint('ck_project_stage_edges', 'project_stage_transitions', type_='check')


def upgrade():
    drop_edges()
    check_edges(f'{NON_GATE},{TERMINATION}')
    op.execute('''CREATE TABLE project_termination_confirmations (
        approval_id uuid PRIMARY KEY REFERENCES project_transition_intents(approval_id),
        signer_id uuid NOT NULL REFERENCES persons(id), assignment_id uuid NOT NULL REFERENCES project_members(id),
        payload_hash char(64) NOT NULL CHECK(payload_hash ~ '^[a-f0-9]{64}$'),
        reason text NOT NULL CHECK(length(btrim(reason))>=4), signed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        request_id uuid NOT NULL UNIQUE
    )''')
    op.execute('''CREATE TRIGGER immutable_project_termination_confirmations
        BEFORE UPDATE OR DELETE OR TRUNCATE ON project_termination_confirmations
        FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE project_stage_transitions,project_transition_intents,project_termination_confirmations IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text(f'''SELECT EXISTS(SELECT FROM project_termination_confirmations)
        OR EXISTS(SELECT FROM project_transition_intents WHERE (from_stage,to_stage) IN ({TERMINATION}))''')):
        raise RuntimeError('Refusing to discard review termination history')
    op.drop_table('project_termination_confirmations')
    drop_edges()
    check_edges(NON_GATE)
