"""Store immutable policy approvals, lifecycle events and transaction bindings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0039'
down_revision = '0038'

_TABLES = ('policy_authority_grants', 'policy_authority_revocations', 'policy_versions', 'policy_events', 'policy_bindings')


def uuid(name, reference=None, **kwargs):
    return sa.Column(name, sa.Uuid, *([sa.ForeignKey(reference)] if reference else []), **kwargs)


def timestamp(name, *, nullable=False, default=None):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=default)


def string(name, limit):
    return sa.Column(name, sa.String(limit), nullable=False)


def upgrade():
    clock = sa.text('clock_timestamp()')
    generated = sa.text('gen_random_uuid()')
    op.create_table('policy_authority_grants',
        uuid('id', primary_key=True, server_default=generated),
        uuid('org_id', 'organizations.id', nullable=False),
        uuid('person_id', 'persons.id', nullable=False),
        string('capability', 12), string('scope', 64), string('authority_ref', 240), string('authority_hash', 64),
        timestamp('recorded_at', default=clock), timestamp('valid_from'), timestamp('expires_at'),
        sa.CheckConstraint("capability IN ('approve','verify','activate')", name='policy_authority_capability'),
        sa.CheckConstraint('valid_from < expires_at AND expires_at > recorded_at', name='policy_authority_term'))
    op.create_index('policy_authority_lookup', 'policy_authority_grants', ['org_id', 'person_id', 'capability', 'scope'])
    op.create_table('policy_authority_revocations',
        uuid('grant_id', 'policy_authority_grants.id', primary_key=True),
        string('reference', 240), uuid('request_id', nullable=False, unique=True), timestamp('recorded_at', default=clock))
    op.create_table('policy_versions',
        uuid('id', primary_key=True, server_default=generated),
        uuid('org_id', 'organizations.id', nullable=False), uuid('project_id'),
        uuid('approver_id', 'persons.id', nullable=False), uuid('verified_by', 'persons.id', nullable=False),
        uuid('approval_grant_id', 'policy_authority_grants.id', nullable=False),
        uuid('verification_grant_id', 'policy_authority_grants.id', nullable=False),
        uuid('request_id', nullable=False, unique=True),
        string('decision_code', 6), string('version', 64), string('environment', 64), string('evidence_ref', 240),
        string('evidence_hash', 64), string('policy_hash', 64),
        sa.Column('policy', JSONB, nullable=False), sa.Column('synthetic', sa.Boolean, nullable=False),
        timestamp('approved_at'), timestamp('effective_from'), timestamp('effective_until', nullable=True),
        timestamp('recorded_at', default=clock),
        sa.ForeignKeyConstraint(('org_id', 'project_id'), ('projects.org_id', 'projects.id')),
        sa.CheckConstraint('approver_id <> verified_by', name='policy_independent_verification'),
        sa.CheckConstraint('effective_from >= approved_at AND (effective_until IS NULL OR effective_until > effective_from)',
                           name='policy_effective_term'))
    op.create_index('policy_version_scope', 'policy_versions',
        ['org_id', 'project_id', 'environment', 'decision_code', 'version', 'synthetic'], unique=True,
        postgresql_nulls_not_distinct=True)
    op.create_table('policy_events',
        sa.Column('sequence', sa.BigInteger, sa.Identity(), primary_key=True),
        uuid('policy_id', 'policy_versions.id', nullable=False),
        uuid('person_id', 'persons.id', nullable=False),
        uuid('activation_grant_id', 'policy_authority_grants.id', nullable=False),
        uuid('request_id', nullable=False, unique=True),
        string('event', 8), string('reason', 240), timestamp('recorded_at', default=clock),
        sa.UniqueConstraint('policy_id', 'event'),
        sa.CheckConstraint("event IN ('active','revoked')", name='policy_lifecycle_event'))
    op.create_table('policy_bindings',
        uuid('subject_id', nullable=False), uuid('project_id', 'projects.id', nullable=False),
        string('kind', 8), string('action', 64), sa.Column('basis', JSONB, nullable=False),
        sa.PrimaryKeyConstraint('subject_id', 'kind', 'action'),
        sa.CheckConstraint("kind IN ('pending','receipt') AND jsonb_typeof(basis) = 'array'", name='policy_binding_kind'))
    op.execute("""
        CREATE FUNCTION pmo_immutable_policy() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE insufficient_privilege USING MESSAGE='Policy records are append only';
        END $$;
    """)
    for table in _TABLES:
        op.execute(f'''CREATE TRIGGER pmo_immutable_policy BEFORE UPDATE OR DELETE OR TRUNCATE ON {table}
                      FOR EACH STATEMENT EXECUTE FUNCTION pmo_immutable_policy()''')


def downgrade():
    connection = op.get_bind()
    connection.exec_driver_sql('LOCK TABLE ' + ', '.join(_TABLES) + ' IN ACCESS EXCLUSIVE MODE')
    for table in _TABLES:
        if connection.scalar(sa.text(f'SELECT EXISTS (SELECT 1 FROM {table})')):
            raise RuntimeError('Refusing downgrade of populated policy history')
    for table in reversed(_TABLES):
        op.drop_table(table)
    op.execute('DROP FUNCTION pmo_immutable_policy()')
