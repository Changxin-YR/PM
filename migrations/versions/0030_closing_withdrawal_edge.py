"""Enforce the frozen closing rework edge on new records without rewriting history."""
import sqlalchemy as sa
from alembic import op

revision = '0030'
down_revision = '0029'

TABLE = 'project_stage_transitions'
CHECK = 'ck_project_stage_closing_rework'
LEGACY_EDGE = "from_stage = 'closing_review' AND to_stage = 'delivering'"


def upgrade():
    op.execute(f'ALTER TABLE {TABLE} ADD CONSTRAINT {CHECK} CHECK (NOT ({LEGACY_EDGE})) NOT VALID')
    if not op.get_bind().scalar(sa.text(f'SELECT EXISTS (SELECT 1 FROM {TABLE} WHERE {LEGACY_EDGE})')):
        op.execute(f'ALTER TABLE {TABLE} VALIDATE CONSTRAINT {CHECK}')


def downgrade():
    op.drop_constraint(CHECK, TABLE, type_='check')
