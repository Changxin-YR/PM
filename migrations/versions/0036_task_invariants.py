"""Bind strengthening tasks to real sources and seal their completion state."""
from alembic import op

revision = '0036'
down_revision = '0035'

PARENTS = (('review', 'reviews', 'project_id'), ('rule_event', 'rule_events', 'project_id'),
           ('remediation', 'risks', 'project_id'), ('report_analysis', 'reports', 'project_id'),
           ('fact_clarification', 'fact_clarifications', 'fact_id'), ('fact_owner', 'facts', 'project_id'))
MANAGED = "'review','rule_event','remediation','report_analysis','fact_clarification'"


def upgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE')
    for kind, table, _ in PARENTS[:-1]:
        project = 's.project_id'
        lookup = f'{table} s'
        if kind == 'fact_clarification':
            lookup += ' JOIN facts f ON f.id=s.fact_id'
            project = 'f.project_id'
        if c.exec_driver_sql(f'''SELECT EXISTS (SELECT 1 FROM tasks t WHERE t.source_type='{kind}'
                AND NOT EXISTS (SELECT 1 FROM {lookup} WHERE s.id=t.source_id AND {project}=t.project_id))''').scalar_one():
            raise RuntimeError(f'Task migration stopped: historical {kind} source missing or outside the project')

    # The workbench already interpreted legacy review `open` as `todo`. Do not change receipts or versions.
    op.execute(f"UPDATE tasks SET status='todo' WHERE status='open' AND source_type IN ({MANAGED})")
    op.create_check_constraint('ck_task_status', 'tasks', f"source_type NOT IN ({MANAGED}) "
                               "OR status IN ('todo','in_progress','blocked','done','cancelled')")
    op.create_check_constraint('ck_task_completion', 'tasks', f"source_type NOT IN ({MANAGED}) OR "
                               "((status='done' AND completed_at IS NOT NULL AND isfinite(completed_at) "
                               "AND completed_at >= created_at) OR (status<>'done' AND completed_at IS NULL))")
    op.create_index('ix_task_source', 'tasks', ['source_type', 'source_id'])
    op.execute(f'''CREATE FUNCTION pmo_bind_task_source() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        DECLARE source_table text; source_project uuid;
        BEGIN
          IF TG_OP='UPDATE' AND (NEW.id,NEW.project_id,NEW.source_type,NEW.source_id)
             IS DISTINCT FROM (OLD.id,OLD.project_id,OLD.source_type,OLD.source_id) THEN
            RAISE check_violation USING CONSTRAINT='ck_task_source_immutable', MESSAGE='Task source cannot change';
          END IF;
          IF NEW.source_type NOT IN ({MANAGED}) THEN
            RAISE check_violation USING CONSTRAINT='ck_task_source_scope', MESSAGE='Unsupported task source';
          END IF;
          IF TG_OP='UPDATE' THEN RETURN NEW; END IF;
          IF NEW.source_type='fact_clarification' THEN
            EXECUTE format('SELECT f.project_id FROM %I.fact_clarifications k JOIN %I.facts f ON f.id=k.fact_id
                            WHERE k.id=$1 FOR SHARE OF k,f', TG_TABLE_SCHEMA, TG_TABLE_SCHEMA)
                INTO source_project USING NEW.source_id;
          ELSE
            source_table := CASE NEW.source_type WHEN 'review' THEN 'reviews' WHEN 'rule_event' THEN 'rule_events'
                              WHEN 'remediation' THEN 'risks' WHEN 'report_analysis' THEN 'reports' END;
            EXECUTE format('SELECT project_id FROM %I.%I WHERE id=$1 FOR SHARE', TG_TABLE_SCHEMA, source_table)
                INTO source_project USING NEW.source_id;
          END IF;
          IF source_project IS DISTINCT FROM NEW.project_id THEN
            RAISE check_violation USING CONSTRAINT='ck_task_source_scope', MESSAGE='Task source is not in the project';
          END IF;
          RETURN NEW;
        END $$''')
    op.execute('''CREATE FUNCTION pmo_preserve_task_source() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        DECLARE in_use boolean;
        BEGIN
          IF TG_OP='UPDATE' THEN
            IF TG_ARGV[0]='fact_clarification' THEN
              IF (OLD.id,OLD.fact_id) IS NOT DISTINCT FROM (NEW.id,NEW.fact_id) THEN RETURN NEW; END IF;
            ELSIF (OLD.id,OLD.project_id) IS NOT DISTINCT FROM (NEW.id,NEW.project_id) THEN RETURN NEW;
            END IF;
          END IF;
          IF TG_ARGV[0]='fact_owner' THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.tasks t JOIN %I.fact_clarifications k ON k.id=t.source_id
                            WHERE t.source_type=''fact_clarification'' AND k.fact_id=$1)', TG_TABLE_SCHEMA, TG_TABLE_SCHEMA)
                INTO in_use USING OLD.id;
          ELSE
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.tasks WHERE source_type=$1 AND source_id=$2)', TG_TABLE_SCHEMA)
                INTO in_use USING TG_ARGV[0], OLD.id;
          END IF;
          IF in_use THEN
            RAISE check_violation USING CONSTRAINT='ck_task_source_in_use', MESSAGE='Referenced task source cannot move or be removed';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$''')
    op.execute('CREATE TRIGGER task_source_write_guard BEFORE INSERT OR UPDATE ON tasks '
               'FOR EACH ROW EXECUTE FUNCTION pmo_bind_task_source()')
    for kind, table, key in PARENTS:
        op.execute(f'CREATE TRIGGER task_source_parent_guard BEFORE UPDATE OF id,{key} OR DELETE ON {table} '
                   f"FOR EACH ROW EXECUTE FUNCTION pmo_preserve_task_source('{kind}')")
    op.execute('CREATE TRIGGER archived_project_write_guard BEFORE INSERT OR UPDATE OR DELETE ON tasks '
               'FOR EACH ROW EXECUTE FUNCTION pmo_guard_archived_records()')


def downgrade():
    op.execute('DROP TRIGGER archived_project_write_guard ON tasks')
    for _, table, _ in reversed(PARENTS):
        op.execute(f'DROP TRIGGER task_source_parent_guard ON {table}')
    op.execute('DROP TRIGGER task_source_write_guard ON tasks')
    op.execute('DROP FUNCTION pmo_preserve_task_source()')
    op.execute('DROP FUNCTION pmo_bind_task_source()')
    op.drop_index('ix_task_source', table_name='tasks')
    op.drop_constraint('ck_task_completion', 'tasks', type_='check')
    op.drop_constraint('ck_task_status', 'tasks', type_='check')
