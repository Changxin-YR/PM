from functools import lru_cache
import os
from sqlalchemy import create_engine

@lru_cache
def engine():
    return create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True, hide_parameters=True)
