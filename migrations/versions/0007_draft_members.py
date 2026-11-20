"""Record existing draft initiators, without granting any governance role."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'


def upgrade():
    op.execute('LOCK TABLE projects,project_members IN SHARE ROW EXCLUSIVE MODE')
    op.execute('''CREATE FUNCTION pmo_register_draft_initiator() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS(SELECT FROM persons WHERE id=NEW.initiator_id AND org_id=NEW.org_id) THEN
            RAISE EXCEPTION 'invalid project initiator scope';
          END IF;
          INSERT INTO project_members(project_id,person_id,assigned_by,role,assignment_status,accepted_at)
          VALUES(NEW.id,NEW.initiator_id,NEW.initiator_id,'initiator','accepted',NEW.created_at);
          RETURN NEW;
        END $$''')
    op.execute("CREATE TRIGGER register_draft_initiator AFTER INSERT ON projects FOR EACH ROW WHEN (NEW.stage='drafting') EXECUTE FUNCTION pmo_register_draft_initiator()")
    op.execute('''INSERT INTO project_members(project_id,person_id,assigned_by,role,assignment_status,accepted_at)
        SELECT p.id,p.initiator_id,p.initiator_id,'initiator','accepted',p.created_at
        FROM projects p JOIN persons n ON n.id=p.initiator_id AND n.org_id=p.org_id
        WHERE p.stage='drafting' ON CONFLICT(project_id,person_id,role) DO NOTHING''')


def downgrade():
    c = op.get_bind()
    c.exec_driver_sql('LOCK TABLE project_members IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text("SELECT EXISTS(SELECT FROM project_members WHERE role='initiator')")):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.execute('DROP TRIGGER register_draft_initiator ON projects')
    op.execute('DROP FUNCTION pmo_register_draft_initiator()')
