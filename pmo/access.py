from typing import Annotated

from fastapi import Depends, Header, Request
from pydantic import Field, ValidationError

from .dto import Strict
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


class ListFilters(Strict):
    stage: str = ''
    keyword: str = Field(default='', max_length=160)


def parse_list_filters(request: Request) -> ListFilters:
    fields: dict[str, str] = {}
    for name, value in request.query_params.multi_items():
        if name == 'filters':
            raise Failure(422, 'VALIDATION_FAILED')
        if not name.startswith('filters['):
            continue
        key = name[len('filters['):-1]
        if not name.endswith(']') or key in fields:
            raise Failure(422, 'VALIDATION_FAILED')
        fields[key] = value
    try:
        return ListFilters.model_validate(fields)
    except ValidationError:
        raise Failure(422, 'VALIDATION_FAILED') from None


Filters = Annotated[ListFilters, Depends(parse_list_filters)]
DEEP_OBJECT_FILTERS = {'parameters': [{
    'name': 'filters', 'in': 'query', 'required': False, 'style': 'deepObject', 'explode': True,
    'schema': ListFilters.model_json_schema(),
}]}
