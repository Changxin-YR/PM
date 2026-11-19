from uuid import UUID
from fastapi import APIRouter
from . import milestones as svc
from .access import Auth, Key, Revision
from .contracts import Result

router = APIRouter(prefix='/api/v1', tags=['Milestones'])


@router.get('/projects/{projectId}/milestones',operation_id='pmo_milestone_list',response_model_exclude_none=True)
def listing(projectId:UUID, authorization:Auth)->Result[svc.Page]:
    return svc.listing(authorization,projectId)


@router.post('/projects/{projectId}/milestones',operation_id='pmo_milestone_create',response_model_exclude_none=True)
def create(projectId:UUID, command:svc.Create, authorization:Auth, key:Key)->Result[svc.View]:
    return svc.save(authorization,key,command,pid=projectId)


@router.patch('/milestones/{milestoneId}',operation_id='pmo_milestone_draft_update',response_model_exclude_none=True)
def update(milestoneId:UUID, command:svc.Update, authorization:Auth, key:Key, expected:Revision)->Result[svc.View]:
    return svc.save(authorization,key,command,mid=milestoneId,expected=int(expected))
