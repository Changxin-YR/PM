import os
from alembic import context
from sqlalchemy import create_engine
from pmo.schema_v1 import metadata

with create_engine(os.environ['DATABASE_URL']).connect() as connection:
    if connection.dialect.name != 'postgresql':
        raise RuntimeError('PostgreSQL required')
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
