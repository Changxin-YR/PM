"""Record non-gate transitions without manufacturing review rows."""
import sqlalchemy as sa
from alembic import op

revision = '0019'
down_revision = '0018'

REVIEW_EDGES = """('drafting','charter_review'),('designing','feasibility_review'),('delivering','closing_review'),
    ('charter_review','designing'),('charter_review','drafting'),('charter_review','rejected'),
    ('feasibility_review','executing'),('feasibility_review','designing'),
    ('closing_review','closed'),('closing_review','executing'),('closing_review','delivering')"""
NON_GATE = """('rejected','drafting'),('rejected','terminated'),('executing','on_hold'),('on_hold','executing'),
    ('executing','delivering'),('delivering','executing'),('executing','terminated'),('on_hold','terminated'),
    ('closed','retrospective'),('terminated','retrospective'),('retrospective','archived')"""


def upgrade():
    op.alter_column('decisions', 'review_id', nullable=True)
    op.execute(f'''CREATE TABLE project_transition_intents (
        approval_id uuid PRIMARY KEY REFERENCES approval_requests(id),
        from_stage text NOT NULL, to_stage text NOT NULL,
        basis jsonb NOT NULL CHECK(jsonb_typeof(basis)='object'),
        source_refs jsonb NOT NULL CHECK(jsonb_typeof(source_refs)='array'),
        reason text NOT NULL CHECK(length(btrim(reason))>=3),
        actor_type text NOT NULL CHECK(actor_type IN ('human','agent')),
        requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK((from_stage,to_stage) IN ({NON_GATE}))
    )''')
    op.execute('''CREATE TABLE project_transition_outcomes (
        approval_id uuid PRIMARY KEY REFERENCES project_transition_intents(approval_id),
        decision_id uuid NOT NULL UNIQUE REFERENCES decisions(id),
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
        payload_hash char(64) NOT NULL CHECK(payload_hash ~ '^[a-f0-9]{64}$'), request_id uuid NOT NULL UNIQUE
    )''')
    for table in ('project_transition_intents', 'project_transition_outcomes'):
        op.execute(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    op.alter_column('project_stage_transitions', 'review_id', nullable=True)
    op.execute('ALTER TABLE project_stage_transitions ADD COLUMN approval_request_id uuid UNIQUE REFERENCES project_transition_intents(approval_id)')
    op.drop_constraint('ck_project_stage_edges', 'project_stage_transitions', type_='check')
    op.create_check_constraint('ck_project_stage_edges', 'project_stage_transitions',
        f'''(review_id IS NOT NULL AND approval_request_id IS NULL AND (from_stage,to_stage) IN ({REVIEW_EDGES}))
        OR (review_id IS NULL AND approval_request_id IS NOT NULL AND actor_type='human' AND (from_stage,to_stage) IN ({NON_GATE}))''')
    op.execute('''CREATE UNIQUE INDEX uq_project_charter_resubmission ON project_stage_transitions(project_id)
        WHERE from_stage='rejected' AND to_stage='drafting' ''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE project_stage_transitions,project_transition_intents,project_transition_outcomes,decisions IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM project_transition_intents) OR EXISTS(SELECT FROM decisions WHERE review_id IS NULL)')):
        raise RuntimeError('Refusing destructive downgrade of populated lifecycle history')
    op.drop_index('uq_project_charter_resubmission', table_name='project_stage_transitions')
    op.drop_constraint('ck_project_stage_edges', 'project_stage_transitions', type_='check')
    op.drop_column('project_stage_transitions', 'approval_request_id')
    op.alter_column('project_stage_transitions', 'review_id', nullable=False)
    op.create_check_constraint('ck_project_stage_edges', 'project_stage_transitions', f'(from_stage,to_stage) IN ({REVIEW_EDGES})')
    op.drop_table('project_transition_outcomes')
    op.drop_table('project_transition_intents')
    op.alter_column('decisions', 'review_id', nullable=False)
