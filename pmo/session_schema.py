import sqlalchemy as sa

metadata=sa.MetaData()
sessions=sa.Table('web_sessions',metadata,
    sa.Column('token_hash',sa.CHAR(64),primary_key=True),
    sa.Column('csrf_hash',sa.CHAR(64),nullable=False),
    sa.Column('account_id',sa.Uuid,nullable=False,index=True),
    sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
    sa.Column('revoked_at',sa.DateTime(timezone=True)))
# The historical 0002 migration imports this metadata. 0025 adds the UUID separately.
SESSION_ID = sa.literal_column('web_sessions.session_id', sa.Uuid())
attempts=sa.Table('login_windows',metadata,
    sa.Column('bucket',sa.CHAR(64),primary_key=True),
    sa.Column('started_at',sa.DateTime(timezone=True),nullable=False),
    sa.Column('attempt_count',sa.Integer,nullable=False))
