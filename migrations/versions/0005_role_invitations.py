"""Separate nominations and appointment authority from effective memberships."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'


def upgrade():
    op.execute('''CREATE TABLE appointment_authorities (
        org_id uuid NOT NULL REFERENCES organizations(id), person_id uuid NOT NULL REFERENCES persons(id),
        role text NOT NULL CHECK (role IN ('sponsor','pm')), authority_ref text NOT NULL CHECK (length(trim(authority_ref))>0),
        expires_at timestamptz NOT NULL, revoked_at timestamptz, PRIMARY KEY(org_id,person_id,role))''')
    op.execute('''CREATE TABLE role_invitations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL REFERENCES projects(id),
        person_id uuid NOT NULL REFERENCES persons(id), proposer_id uuid NOT NULL REFERENCES persons(id),
        role text NOT NULL CHECK (role IN ('sponsor','pm')), status text NOT NULL CHECK(status IN ('proposed','invited','accepted','declined')),
        revision bigint NOT NULL DEFAULT 1 CHECK(revision >= 1), issuer_id uuid REFERENCES persons(id),
        expires_at timestamptz, decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(issuer_id <> proposer_id AND issuer_id <> person_id),
        CHECK((status='proposed') = (issuer_id IS NULL)), CHECK((status='proposed') = (expires_at IS NULL)),
        CHECK((status IN ('accepted','declined')) = (decided_at IS NOT NULL)))''')
    op.execute('CREATE INDEX ix_role_invitation_project ON role_invitations(project_id,role,status)')
    op.execute('CREATE INDEX ix_role_invitation_inbox ON role_invitations(person_id,status)')


def downgrade():
    c=op.get_bind()
    c.exec_driver_sql('LOCK TABLE role_invitations,appointment_authorities IN ACCESS EXCLUSIVE MODE')
    if c.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM role_invitations) OR EXISTS(SELECT 1 FROM appointment_authorities)')):
        raise RuntimeError('Refusing destructive downgrade of populated PMO database')
    op.drop_table('role_invitations')
    op.drop_table('appointment_authorities')
