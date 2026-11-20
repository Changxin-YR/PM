"""Append-only review submissions and their project stage transitions."""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'


def upgrade():
    op.execute('''CREATE TABLE review_submission_snapshots (
        review_id uuid PRIMARY KEY REFERENCES reviews(id),
        rule_version text NOT NULL, snapshot_hash char(64) NOT NULL CHECK(snapshot_hash ~ '^[a-f0-9]{64}$'),
        materials jsonb NOT NULL CHECK(jsonb_typeof(materials)='object'),
        source_refs jsonb NOT NULL CHECK(jsonb_typeof(source_refs)='array'),
        CHECK(materials ? 'reviewers' AND jsonb_typeof(materials->'reviewers')='array' AND jsonb_array_length(materials->'reviewers')>0)
    )''')
    op.execute('''CREATE TABLE project_stage_transitions (
        project_id uuid NOT NULL REFERENCES projects(id),
        from_revision bigint NOT NULL CHECK(from_revision>=1),
        to_revision bigint NOT NULL CHECK(to_revision=from_revision+1),
        from_stage text NOT NULL, to_stage text NOT NULL,
        review_id uuid NOT NULL UNIQUE REFERENCES review_submission_snapshots(review_id),
        actor_id uuid NOT NULL REFERENCES persons(id),
        actor_type text NOT NULL CHECK(actor_type IN ('human','agent')),
        request_id uuid NOT NULL UNIQUE, transitioned_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(project_id,from_revision),
        CHECK((from_stage,to_stage) IN (('drafting','charter_review'),('designing','feasibility_review'),('delivering','closing_review')))
    )''')
    for table in ('review_submission_snapshots', 'project_stage_transitions'):
        op.execute(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} '
                   'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE review_submission_snapshots,project_stage_transitions IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT FROM review_submission_snapshots) OR EXISTS(SELECT FROM project_stage_transitions)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.drop_table('project_stage_transitions')
    op.drop_table('review_submission_snapshots')
