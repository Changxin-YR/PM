from typing import Annotated
from uuid import UUID
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from .contracts import CreateProject, ProjectView, ProjectDetail, Page, Result, UpdateDraft
from .identity import Failure
from . import projects

app=FastAPI(title='PMO Lite',version='0.1.0')
Auth=Annotated[str|None, Header(alias='Authorization')]

@app.exception_handler(Failure)
async def failure(_:Request,e:Failure):
    return JSONResponse({'code':e.code,'message':e.code},status_code=e.status)

@app.get('/api/v1/projects',operation_id='pmo_project_list')
def listing(authorization:Auth=None,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100),stage:str|None=Query(None,alias='filters[stage]'),keyword:str|None=Query(None,alias='filters[keyword]',max_length=160))->Result[Page]:
    return projects.listing(authorization,page,pageSize,stage,keyword)

@app.get('/api/v1/projects/{projectId}',operation_id='pmo_project_get')
def detail(projectId:UUID,authorization:Auth=None)->Result[ProjectDetail]:
    return projects.detail(authorization,projectId)

@app.post('/api/v1/projects',operation_id='pmo_project_create')
def create(command:CreateProject,key:Annotated[str,Header(alias='Idempotency-Key',min_length=8,max_length=128)],authorization:Auth=None)->Result[ProjectView]:
    return projects.create(authorization,command,key)

@app.patch('/api/v1/projects/{projectId}',operation_id='pmo_project_draft_update')
def update(projectId:UUID,command:UpdateDraft,key:Annotated[str,Header(alias='Idempotency-Key',min_length=8,max_length=128)],expected:Annotated[str,Header(alias='If-Match',pattern=r'^[1-9][0-9]{0,18}$')],authorization:Auth=None)->Result[ProjectView]:
    return projects.update_draft(authorization,projectId,int(expected),command,key)
