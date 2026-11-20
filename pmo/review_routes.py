from uuid import UUID

from fastapi import APIRouter, Response

from . import reviews
from .access import Auth, Key, Revision
from .contracts import Result
from .gates import Check, Gate

router = APIRouter(prefix='/api/v1', tags=['Reviews'])


@router.get('/projects/{projectId}/gates/check', operation_id=reviews.CHECK)
def check(projectId: UUID, gate: Gate, authorization: Auth) -> Result[Check]:
    return reviews.preview(authorization, projectId, gate, prepare=False)


@router.get('/projects/{projectId}/reviews/prepare', operation_id=reviews.PREPARE)
def prepare(projectId: UUID, gate: Gate, response: Response, authorization: Auth) -> Result[reviews.Preparation]:
    result, can_submit, revision = reviews.preview(authorization, projectId, gate, prepare=True)
    response.headers['X-PMO-Can-Submit'] = 'true' if can_submit else 'false'
    response.headers['X-PMO-Project-Revision'] = str(revision)
    return result


@router.get('/projects/{projectId}/reviews', operation_id=reviews.LIST, response_model_exclude_none=True)
def listing(projectId: UUID, authorization: Auth) -> Result[reviews.ReviewPage]:
    return reviews.listing(authorization, projectId)


@router.post('/projects/{projectId}/reviews', operation_id=reviews.SUBMIT, response_model_exclude_none=True)
def submit(projectId: UUID, command: reviews.Submit, authorization: Auth, key: Key, expected: Revision) -> Result[reviews.ReviewView]:
    return reviews.submit(authorization, projectId, command.reviewType, int(expected), key)
