"""Store human reinforcement responses and append-only scoring calibration."""
from alembic import op

revision = '0041'
down_revision = '0040'

TABLES = (
    'review_reinforcement_responses',
    'review_calibration_plans',
    'review_calibration_results',
)


def upgrade():
    op.execute('ALTER TABLE review_strengthening_tasks ADD COLUMN evidence_required boolean')
    op.execute("ALTER TABLE project_retrospectives ADD COLUMN calibration_deviations jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute('''CREATE TABLE review_reinforcement_responses (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        task_id uuid NOT NULL REFERENCES review_strengthening_tasks(task_id),
        revision bigint NOT NULL CHECK(revision > 1),
        submitted_by uuid NOT NULL REFERENCES persons(id),
        submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        response_text text NOT NULL CHECK(length(trim(response_text)) BETWEEN 4 AND 6000),
        evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='array'),
        UNIQUE(task_id, revision)
    )''')
    op.execute('''CREATE TABLE review_calibration_plans (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id uuid NOT NULL REFERENCES projects(id),
        revision bigint NOT NULL CHECK(revision > 0),
        submitted_by uuid NOT NULL REFERENCES persons(id),
        submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        dimensions jsonb NOT NULL CHECK(jsonb_typeof(dimensions)='object'),
        UNIQUE(project_id, revision)
    )''')
    op.execute('''CREATE TABLE review_calibration_results (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        review_id uuid NOT NULL REFERENCES review_decision_snapshots(review_id),
        dimension text NOT NULL,
        revision bigint NOT NULL CHECK(revision > 0),
        submitted_by uuid NOT NULL REFERENCES persons(id),
        submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        evaluation jsonb NOT NULL CHECK(jsonb_typeof(evaluation)='object'),
        UNIQUE(review_id, dimension, revision)
    )''')
    for table in TABLES:
        op.execute(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE ' + ', '.join((*TABLES, 'review_strengthening_tasks',
                                               'project_retrospectives')) + ' IN ACCESS EXCLUSIVE MODE')
    if c.exec_driver_sql('SELECT ' + ' OR '.join(f'EXISTS(SELECT FROM {table})' for table in TABLES)
                         + " OR EXISTS(SELECT FROM project_retrospectives WHERE calibration_deviations <> '{}'::jsonb)"
                         + ' OR EXISTS(SELECT FROM review_strengthening_tasks WHERE evidence_required IS NOT NULL)').scalar():
        raise RuntimeError('Refusing destructive downgrade of review evidence')
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column('project_retrospectives', 'calibration_deviations')
    op.drop_column('review_strengthening_tasks', 'evidence_required')
