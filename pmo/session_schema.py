import sqlalchemy as sa

metadata=sa.MetaData()
sessions=sa.Table('web_sessions',metadata,
    sa.Column('token_hash',sa.CHAR(64),primary_key=True),
    sa.Column('csrf_hash',sa.CHAR(64),nullable=False),
    sa.Column('account_id',sa.Uuid,nullable=False,index=True),
    sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
    sa.Column('revoked_at',sa.DateTime(timezone=True)))
attempts=sa.Table('login_windows',metadata,
    sa.Column('bucket',sa.CHAR(64),primary_key=True),
    sa.Column('started_at',sa.DateTime(timezone=True),nullable=False),
    sa.Column('attempt_count',sa.Integer,nullable=False))
