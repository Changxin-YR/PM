from uuid import UUID
from fastapi import APIRouter, Query
from . import requirements as svc
from .access import Auth, DEEP_OBJECT_FILTERS, Filters, Key, Revision
from .contracts import Result

router=APIRouter(prefix='/api/v1',tags=['Requirements'])


@router.get('/projects/{projectId}/requirements',operation_id='pmo_requirement_list',response_model_exclude_none=True,
            openapi_extra=DEEP_OBJECT_FILTERS)
def listing(projectId:UUID,authorization:Auth,filters:Filters,page:int=Query(1,ge=1),
            pageSize:int=Query(20,ge=1,le=100))->Result[svc.Page]:
    return svc.listing(authorization,projectId,page,pageSize,filters.stage,filters.keyword)


@router.post('/projects/{projectId}/requirements',operation_id='pmo_requirement_create',response_model_exclude_none=True)
def create(projectId:UUID,command:svc.Create,authorization:Auth,key:Key)->Result[svc.View]:
    return svc.save(authorization,key,command,pid=projectId)


@router.patch('/requirements/{requirementId}',operation_id='pmo_requirement_draft_update',response_model_exclude_none=True)
def update(requirementId:UUID,command:svc.Update,authorization:Auth,key:Key,expected:Revision)->Result[svc.View]:
    return svc.save(authorization,key,command,req_id=requirementId,expected=int(expected))
