from uuid import UUID
from fastapi import APIRouter
from .access import Auth, Key, Revision
from .contracts import Result, CharterSummary
from .charter_models import Draft, Save
from . import charters

router=APIRouter(prefix='/api/v1/projects',tags=['Charter'])


@router.get('/{projectId}/charter',operation_id='human_charter_get',response_model_exclude_none=True)
def read(projectId:UUID,authorization:Auth)->Result[Draft]:
    return charters.read(authorization,projectId)


@router.put('/{projectId}/charter',operation_id='pmo_charter_save',response_model_exclude_none=True)
def save(projectId:UUID,command:Save,authorization:Auth,key:Key,expected:Revision)->Result[CharterSummary]:
    return charters.save(authorization,projectId,int(expected),key,command)
