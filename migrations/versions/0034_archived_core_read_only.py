"""Reject project and protected record writes after archival."""
from alembic import op

revision = '0034'
down_revision = '0033'

PROJECT_RECORDS = (
    'charters', 'requirements', 'requirement_baseline_signatures', 'milestones',
    'cost_entries', 'cost_baseline_history', 'cost_plan_versions', 'cost_postings', 'cost_reconciliations',
    'reviews', 'scorecards', 'decisions', 'change_entries', 'lessons', 'project_retrospectives',
)


def upgrade():
    op.execute('''CREATE FUNCTION pmo_guard_archived_records() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog AS $$
        DECLARE old_project uuid; new_project uuid; review_project uuid; locked_stage text;
        BEGIN
          IF TG_TABLE_NAME='projects' THEN
            locked_stage := OLD.stage;
          ELSE
            IF TG_OP != 'INSERT' THEN old_project := OLD.project_id; END IF;
            IF TG_OP != 'DELETE' THEN new_project := NEW.project_id; END IF;

            IF TG_OP='INSERT' AND TG_TABLE_NAME IN ('scorecards','decisions') THEN
              IF NEW.review_id IS NOT NULL THEN
                EXECUTE format('SELECT project_id FROM %I.reviews WHERE id=$1', TG_TABLE_SCHEMA)
                  INTO review_project USING NEW.review_id;
                IF review_project IS NOT NULL AND review_project IS DISTINCT FROM new_project THEN
                  RAISE check_violation USING CONSTRAINT='ck_review_record_project_scope',
                    MESSAGE='Review and record must belong to the same project';
                END IF;
              END IF;
            END IF;

            -- Conflict with a concurrent stage UPDATE even when it does not change the key.
            FOR locked_stage IN EXECUTE format(
              'SELECT stage FROM %I.projects WHERE id IN ($1,$2) ORDER BY id FOR SHARE', TG_TABLE_SCHEMA)
              USING old_project, new_project
            LOOP
              EXIT WHEN locked_stage='archived';
            END LOOP;
          END IF;
          IF locked_stage='archived' THEN
            RAISE check_violation USING CONSTRAINT='ck_archived_project_read_only',
              MESSAGE='Archived project records are read-only';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$''')
    op.execute('CREATE TRIGGER archived_project_write_guard BEFORE UPDATE OR DELETE ON projects '
               'FOR EACH ROW EXECUTE FUNCTION pmo_guard_archived_records()')
    for table in PROJECT_RECORDS:
        op.execute(f'CREATE TRIGGER archived_project_write_guard BEFORE INSERT OR UPDATE OR DELETE ON {table} '
                   'FOR EACH ROW EXECUTE FUNCTION pmo_guard_archived_records()')


def downgrade():
    for table in (*reversed(PROJECT_RECORDS), 'projects'):
        op.execute(f'DROP TRIGGER archived_project_write_guard ON {table}')
    op.execute('DROP FUNCTION pmo_guard_archived_records()')
