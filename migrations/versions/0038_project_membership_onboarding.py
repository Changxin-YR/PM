"""Allow independently authorized invitations to non-singleton project roles."""
import sqlalchemy as sa
from alembic import op

revision = '0038'
down_revision = '0037'

NEW_ROLES = "'member','reviewer','client_representative'"
TABLES = ('appointment_authorities', 'role_invitations')


def upgrade():
    for table in TABLES:
        op.drop_constraint(f'{table}_role_check', table, type_='check')
        op.create_check_constraint(f'{table}_role_check', table, f"role IN ('pm','sponsor',{NEW_ROLES})")
    op.execute('ALTER TABLE appointment_authorities ADD COLUMN generation uuid NOT NULL DEFAULT gen_random_uuid()')
    op.execute('ALTER TABLE role_invitations ADD COLUMN issuer_authority_generation uuid')
    op.execute('''UPDATE role_invitations AS invitation
        SET issuer_authority_generation = grant_record.generation
        FROM projects AS project, appointment_authorities AS grant_record
        WHERE project.id = invitation.project_id AND project.org_id = grant_record.org_id
        AND invitation.issuer_id = grant_record.person_id AND invitation.role = grant_record.role''')
    op.execute('''CREATE TABLE project_member_revocations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        member_id uuid NOT NULL REFERENCES project_members(id),
        revoked_by uuid NOT NULL REFERENCES persons(id),
        revoked_at timestamptz NOT NULL,
        reason varchar(500) NOT NULL CHECK (length(trim(reason)) >= 4),
        authority_ref text NOT NULL CHECK (length(trim(authority_ref)) > 0)
    )''')
    op.execute('''CREATE TABLE onboarding_authority_records (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id uuid NOT NULL REFERENCES organizations(id),
        project_id uuid REFERENCES projects(id),
        person_id uuid NOT NULL REFERENCES persons(id),
        recorded_by uuid NOT NULL REFERENCES persons(id),
        action text NOT NULL,
        authority_ref text NOT NULL CHECK (length(trim(authority_ref)) > 0),
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )''')


def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql('LOCK TABLE appointment_authorities, role_invitations, project_member_revocations, onboarding_authority_records IN ACCESS EXCLUSIVE MODE')
    for table in TABLES:
        if connection.scalar(sa.text(f'SELECT EXISTS (SELECT 1 FROM {table} WHERE role IN ({NEW_ROLES}))')):
            raise RuntimeError('Refusing downgrade of populated project onboarding roles')
    if connection.scalar(sa.text('SELECT EXISTS (SELECT 1 FROM project_member_revocations) OR EXISTS (SELECT 1 FROM onboarding_authority_records)')):
        raise RuntimeError('Refusing downgrade of project membership history')
    op.drop_table('onboarding_authority_records')
    op.drop_table('project_member_revocations')
    op.drop_column('role_invitations', 'issuer_authority_generation')
    op.drop_column('appointment_authorities', 'generation')
    for table in TABLES:
        op.drop_constraint(f'{table}_role_check', table, type_='check')
        op.create_check_constraint(f'{table}_role_check', table, "role IN ('pm','sponsor')")
