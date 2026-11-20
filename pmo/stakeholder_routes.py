from uuid import UUID
from fastapi import APIRouter, Query
from .access import Auth, Key
from .contracts import Result
from . import stakeholders
from .stakeholder_models import ContactPage, ContactSummary, CreateContact

router = APIRouter(prefix='/api/v1/projects/{projectId}/stakeholders', tags=['Stakeholders'])


@router.post('', operation_id='pmo_stakeholder_create', response_model_exclude_none=True)
def create(projectId: UUID, command: CreateContact, key: Key, authorization: Auth) -> Result[ContactSummary]:
    return stakeholders.create(authorization, projectId, command, key)


@router.get('', operation_id='human_stakeholder_list', response_model_exclude_none=True)
def listing(projectId: UUID, authorization: Auth, page: int = Query(1, ge=1),
            pageSize: int = Query(20, ge=1, le=100)) -> Result[ContactPage]:
    return stakeholders.listing(authorization, projectId, page, pageSize)
