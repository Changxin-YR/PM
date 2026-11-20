"""Append-only score versions, reconsideration, confirmations and gate decisions."""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'

TABLES = ('review_appeals', 'review_confirmations', 'review_decision_snapshots', 'review_strengthening_tasks')
EDGES = """('drafting','charter_review'),('designing','feasibility_review'),('delivering','closing_review'),
    ('charter_review','designing'),('charter_review','drafting'),('charter_review','rejected'),
    ('feasibility_review','executing'),('feasibility_review','designing'),
    ('closing_review','closed'),('closing_review','executing'),('closing_review','delivering')"""
ENTRY = "from_stage IN ('drafting','designing','delivering')"


def upgrade():
    op.drop_constraint('idempotency_keys_actor_id_operation_idempotency_key_key', 'idempotency_keys', type_='unique')
    op.create_unique_constraint('uq_idempotency_subject_operation_key', 'idempotency_keys',
                                ['actor_type', 'actor_id', 'operation', 'idempotency_key'])
    op.drop_constraint('project_stage_transitions_review_id_key', 'project_stage_transitions', type_='unique')
    op.drop_constraint('project_stage_transitions_check1', 'project_stage_transitions', type_='check')
    op.create_check_constraint('ck_project_stage_edges', 'project_stage_transitions', f'(from_stage,to_stage) IN ({EDGES})')
    op.execute(f'CREATE UNIQUE INDEX uq_review_entry ON project_stage_transitions(review_id) WHERE {ENTRY}')
    op.execute(f'CREATE UNIQUE INDEX uq_review_exit ON project_stage_transitions(review_id) WHERE NOT ({ENTRY})')
    op.execute("CREATE UNIQUE INDEX uq_pending_review_per_project ON reviews(project_id) WHERE status='pending'")
    op.execute('''CREATE TABLE review_appeals (
        review_id uuid PRIMARY KEY REFERENCES review_submission_snapshots(review_id),
        requested_by uuid NOT NULL REFERENCES persons(id), requested_at timestamptz NOT NULL DEFAULT now(),
        reason text NOT NULL CHECK(length(btrim(reason))>=4), before_hash char(64) NOT NULL,
        request_id uuid NOT NULL UNIQUE, prior_scorecards jsonb NOT NULL CHECK(jsonb_typeof(prior_scorecards)='array')
    )''')
    op.execute('''CREATE TABLE review_confirmations (
        review_id uuid NOT NULL REFERENCES review_submission_snapshots(review_id),
        role text NOT NULL CHECK(role IN ('sponsor','initiator')), score_hash char(64) NOT NULL,
        confirmed_by uuid NOT NULL REFERENCES persons(id), confirmed_at timestamptz NOT NULL DEFAULT now(),
        request_id uuid NOT NULL UNIQUE, PRIMARY KEY(review_id,role,score_hash)
    )''')
    op.execute('''CREATE TABLE review_decision_snapshots (
        decision_id uuid PRIMARY KEY REFERENCES decisions(id),
        review_id uuid NOT NULL UNIQUE REFERENCES review_submission_snapshots(review_id),
        approval_request_id uuid NOT NULL UNIQUE REFERENCES approval_requests(id),
        rule_version text NOT NULL, material_hash char(64) NOT NULL, payload_hash char(64) NOT NULL,
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'), request_id uuid NOT NULL UNIQUE
    )''')
    op.execute('''CREATE TABLE review_strengthening_tasks (
        review_id uuid NOT NULL REFERENCES review_submission_snapshots(review_id),
        task_id uuid NOT NULL UNIQUE REFERENCES tasks(id), item_key text NOT NULL,
        PRIMARY KEY(review_id,item_key)
    )''')
    for table in (*TABLES, 'scorecards', 'decisions'):
        op.execute(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE ' + ','.join((*TABLES, 'project_stage_transitions', 'reviews', 'scorecards', 'decisions')) + ' IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT ' + ' OR '.join(f'EXISTS(SELECT FROM {table})' for table in TABLES)
                        + f' OR EXISTS(SELECT FROM project_stage_transitions WHERE NOT ({ENTRY}))')):
        raise RuntimeError('Refusing destructive downgrade of populated review records')
    for table in ('scorecards', 'decisions'):
        op.execute(f'DROP TRIGGER immutable_{table} ON {table}')
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_index('uq_pending_review_per_project', table_name='reviews')
    op.drop_index('uq_review_exit', table_name='project_stage_transitions')
    op.drop_index('uq_review_entry', table_name='project_stage_transitions')
    op.drop_constraint('ck_project_stage_edges', 'project_stage_transitions', type_='check')
    op.create_check_constraint('project_stage_transitions_check1', 'project_stage_transitions',
                               "(from_stage,to_stage) IN (('drafting','charter_review'),('designing','feasibility_review'),('delivering','closing_review'))")
    op.create_unique_constraint('project_stage_transitions_review_id_key', 'project_stage_transitions', ['review_id'])
    op.drop_constraint('uq_idempotency_subject_operation_key', 'idempotency_keys', type_='unique')
    op.create_unique_constraint('idempotency_keys_actor_id_operation_idempotency_key_key', 'idempotency_keys',
                                ['actor_id', 'operation', 'idempotency_key'])
