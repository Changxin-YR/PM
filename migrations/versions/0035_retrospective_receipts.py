"""Validate structured retrospective and Lesson completion receipts."""
from alembic import op

revision = '0035'
down_revision = '0034'


def upgrade():
    op.create_check_constraint('ck_lessons_confirmation', 'lessons', '''
        (status = 'draft' AND confirmed_by IS NULL AND confirmed_at IS NULL)
        OR (status = 'confirmed' AND revision >= 2 AND confirmed_by IS NOT NULL
            AND confirmed_by <> created_by AND confirmed_at IS NOT NULL AND confirmed_at >= created_at)
    ''')
    nonblank_text = ' AND '.join(
        f"(jsonb_typeof(answers->{index}) = 'string' AND (answers->>{index}) ~ '[^[:space:]]')"
        for index in range(4)
    )
    op.create_check_constraint('ck_retrospectives_completed_answers', 'project_retrospectives', f'''
        CASE WHEN completed_at IS NULL THEN true
             WHEN jsonb_typeof(answers) <> 'array' THEN false
             ELSE jsonb_array_length(answers) = 4 AND {nonblank_text}
        END
    ''')


def downgrade():
    op.drop_constraint('ck_retrospectives_completed_answers', 'project_retrospectives', type_='check')
    op.drop_constraint('ck_lessons_confirmation', 'lessons', type_='check')
