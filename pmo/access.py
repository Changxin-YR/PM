from typing import Annotated

from fastapi import Depends, Header, Request

from .identity import Browser, Failure


def identity(request: Request, authorization: Annotated[str | None, Header()] = None):
    cookie = request.cookies.get('pmo_session')
    if authorization and cookie:
        raise Failure(401, 'AMBIGUOUS_IDENTITY')
    if cookie:
        return Browser(cookie, request.headers.get('X-CSRF-Token', ''), request.method not in ('GET', 'HEAD'))
    return authorization


Auth = Annotated[Browser | str | None, Depends(identity)]
Key = Annotated[str, Header(alias='Idempotency-Key', min_length=8, max_length=128)]
Revision = Annotated[str, Header(alias='If-Match', pattern=r'^[1-9][0-9]{0,18}$')]
