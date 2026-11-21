"""Unauthenticated probes deliberately expose no dependency details in responses."""
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from alembic.script import ScriptDirectory
from alembic.util import CommandError
from fastapi import APIRouter, Response
from sqlalchemy.exc import SQLAlchemyError

from .contracts import Strict
from .db import engine
from .evidence_storage import root
from .identity import Failure
from .observability import emit
from .request_context import correlation_id

router = APIRouter(prefix='/health', tags=['Operations'])


class Probe(Strict):
    status: Literal['live', 'ready', 'not_ready']


@lru_cache
def expected_heads() -> frozenset[str]:
    directory = Path(__file__).resolve().parents[1] / 'migrations'
    return frozenset(ScriptDirectory(str(directory)).get_heads())


def dependency_problem() -> str | None:
    try:
        expected = expected_heads()
    except (CommandError, OSError, ImportError, RuntimeError):
        return 'migration_files'
    try:
        with engine().begin() as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout='2s'")
            actual = frozenset(connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars())
    except (SQLAlchemyError, KeyError):
        return 'database'
    if not expected or actual != expected:
        return 'migration_head'
    try:
        directory = root()
        if directory.is_dir() and os.access(directory, os.R_OK | os.W_OK | os.X_OK):
            return None
    except (Failure, OSError):
        pass
    return 'private_storage'


@router.get('/live', operation_id='ops_liveness')
def liveness() -> Probe:
    return Probe(status='live')


@router.get('/ready', operation_id='ops_readiness', responses={503: {'model': Probe}})
def readiness(response: Response) -> Probe:
    if problem := dependency_problem():
        emit('readiness.failed', 'warning', requestId=correlation_id(), dependency=problem)
        response.status_code = 503
        return Probe(status='not_ready')
    return Probe(status='ready')
