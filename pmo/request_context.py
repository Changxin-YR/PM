from contextvars import ContextVar
from uuid import uuid4

request_id: ContextVar[str | None] = ContextVar('pmo_request_id', default=None)


def correlation_id() -> str:
    return request_id.get() or str(uuid4())
