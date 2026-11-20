from uuid import UUID

from fastapi import APIRouter, Response

from . import review_outcomes as outcomes, reviews
from .access import Auth, Key, Revision
from .contracts import Result
from .gates import Check, Gate
from .review_scores import Score

router = APIRouter(prefix='/api/v1', tags=['Reviews'])


@router.get('/projects/{projectId}/closing/check', operation_id='human_closing_check')
def closing_check(projectId: UUID, authorization: Auth) -> Result[Check]:
    return reviews.preview(authorization, projectId, 'closing', prepare=False, operation='human_closing_check')


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


@router.get('/reviews/{reviewId}/workbench', operation_id=outcomes.WORKBENCH, response_model_exclude_none=True)
def workbench(reviewId: UUID, authorization: Auth) -> Result[outcomes.Workbench]:
    return outcomes.read_workbench(authorization, reviewId)


@router.get('/reviews/{reviewId}/decisions', operation_id=outcomes.DECISION_GET, response_model_exclude_none=True)
def decisions(reviewId: UUID, authorization: Auth) -> Result[outcomes.Decisions]:
    return outcomes.decision_list(authorization, reviewId)


@router.post('/reviews/{reviewId}/scores', operation_id=outcomes.SCORE, response_model_exclude_none=True)
def score(reviewId: UUID, command: Score, authorization: Auth, key: Key, expected: Revision) -> Result[outcomes.Workbench]:
    return outcomes.write_action(authorization, outcomes.SCORE, reviewId, command, key, int(expected))


@router.post('/reviews/{reviewId}/appeals', operation_id=outcomes.APPEAL, response_model_exclude_none=True)
def appeal(reviewId: UUID, command: outcomes.ReasonCommand, authorization: Auth, key: Key, expected: Revision) -> Result[outcomes.Workbench]:
    return outcomes.write_action(authorization, outcomes.APPEAL, reviewId, command, key, int(expected))


@router.post('/reviews/{reviewId}/confirmations', operation_id=outcomes.CONFIRM, response_model_exclude_none=True)
def confirm(reviewId: UUID, command: outcomes.ConfirmationCommand, authorization: Auth, key: Key, expected: Revision) -> Result[outcomes.Workbench]:
    return outcomes.write_action(authorization, outcomes.CONFIRM, reviewId, command, key, int(expected))


@router.post('/reviews/{reviewId}/decisions', operation_id=outcomes.DECIDE, response_model_exclude_none=True)
def decide(reviewId: UUID, command: outcomes.Decide, authorization: Auth, key: Key, expected: Revision) -> Result[outcomes.Workbench]:
    return outcomes.write_action(authorization, outcomes.DECIDE, reviewId, command, key, int(expected))


@router.post('/reviews/{reviewId}/withdraw', operation_id=outcomes.WITHDRAW, response_model_exclude_none=True)
def withdraw(reviewId: UUID, command: outcomes.ReasonCommand, authorization: Auth, key: Key, expected: Revision) -> Result[outcomes.Workbench]:
    return outcomes.write_action(authorization, outcomes.WITHDRAW, reviewId, command, key, int(expected))
