"""Version category budgets, predictions and verified historical classifications."""
from alembic import op

revision = '0040'
down_revision = '0039'

TABLES = ('category_cost_budgets', 'category_cost_forecasts',
          'category_posting_requests', 'category_posting_classifications')


def upgrade():
    for table, fields in (
        ('category_cost_budgets', """
            phase text NOT NULL CHECK(phase IN ('draft', 'approved')),
            source_ref text NOT NULL,
            totals_ref text NOT NULL
        """),
        ('category_cost_forecasts', """
            observed_at timestamptz NOT NULL CHECK(observed_at <= effective_at),
            budget_revision bigint,
            budget_hash text NOT NULL CHECK(length(budget_hash) = 64),
            actual_hash text NOT NULL CHECK(length(actual_hash) = 64),
            actual_refs jsonb NOT NULL CHECK(jsonb_typeof(actual_refs) = 'array'),
            plan_revision bigint NOT NULL,
            reason text NOT NULL CHECK(length(trim(reason)) >= 3),
            FOREIGN KEY(project_id, budget_revision) REFERENCES category_cost_budgets(project_id, revision),
            FOREIGN KEY(project_id, plan_revision) REFERENCES cost_plan_versions(project_id, revision)
        """),
    ):
        op.execute(f'''CREATE TABLE {table} (
            project_id uuid NOT NULL REFERENCES projects(id), revision bigint NOT NULL CHECK(revision >= 2),
            amounts jsonb NOT NULL CHECK(jsonb_typeof(amounts) = 'object'),
            created_by uuid NOT NULL REFERENCES persons(id),
            effective_at timestamptz NOT NULL,
            PRIMARY KEY(project_id, revision), {fields})''')
        op.execute(f'CREATE INDEX ix_{table}_as_of ON {table}(project_id, effective_at DESC, revision DESC)')
    op.execute('''CREATE TABLE category_posting_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id uuid NOT NULL REFERENCES projects(id),
        posting_id uuid NOT NULL REFERENCES cost_postings(id),
        category text NOT NULL CHECK(category IN ('procurement','travel','cloud','allocation',
            'development','implementation','management')),
        reason text NOT NULL CHECK(length(trim(reason)) >= 3),
        created_by uuid NOT NULL REFERENCES persons(id),
        expected_classification uuid,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(project_id, posting_id, id))''')
    op.execute('''CREATE TABLE category_posting_classifications (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id uuid NOT NULL REFERENCES projects(id),
        posting_id uuid NOT NULL REFERENCES cost_postings(id),
        request_id uuid NOT NULL UNIQUE,
        verified_by uuid NOT NULL REFERENCES persons(id),
        rationale text NOT NULL CHECK(length(trim(rationale)) >= 3),
        effective_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(project_id, posting_id, request_id)
            REFERENCES category_posting_requests(project_id, posting_id, id))''')
    op.execute('CREATE INDEX ix_category_posting_as_of ON category_posting_classifications'
               '(project_id, posting_id, effective_at DESC)')
    for table in TABLES:
        op.execute(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
        op.execute(f'CREATE TRIGGER archived_project_write_guard BEFORE INSERT ON {table} '
                   'FOR EACH ROW EXECUTE FUNCTION pmo_guard_archived_records()')


def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql(f"LOCK TABLE {', '.join(TABLES)} IN ACCESS EXCLUSIVE MODE")
    if any(connection.exec_driver_sql(f'SELECT EXISTS(SELECT FROM {table})').scalar() for table in TABLES):
        raise RuntimeError('Refusing destructive downgrade of populated category cost records')
    for table in reversed(TABLES):
        op.drop_table(table)
