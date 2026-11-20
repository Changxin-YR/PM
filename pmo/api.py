from typing import Annotated
from uuid import UUID
import os
from fastapi import FastAPI, Header, Query, Request, Response
from pydantic import Field, SecretStr
from fastapi.responses import JSONResponse
from .contracts import CreateProject, ProjectView, ProjectDetail, Page, Result, UpdateDraft
from .identity import Browser, Failure
from .contracts import Strict
from . import projects, sessions
from .access import Auth
from .milestone_routes import router as milestone_router
from .requirement_routes import router as requirement_router
from .charter_routes import router as charter_router

app=FastAPI(title='PMO Lite',version='0.1.0')
app.include_router(milestone_router)
app.include_router(requirement_router)
app.include_router(charter_router)

class Login(Strict):
    username:str=Field(min_length=1,max_length=120)
    password:SecretStr=Field(min_length=1,max_length=256)

class LoginResult(Strict):
    csrfToken:str

class CurrentUser(Strict):
    accountId:UUID
    personId:UUID
    orgId:UUID

@app.post('/api/v1/auth/login',operation_id='auth_login')
def login(request:Request,response:Response,command:Login)->LoginResult:
    if request.headers.get('Authorization'): raise Failure(403,'FORBIDDEN')
    local=request.url.hostname in ('127.0.0.1','localhost','::1') and os.getenv('PMO_LOCAL_HTTP')=='1'
    if not local and request.url.scheme!='https': raise Failure(403,'HTTPS_REQUIRED')
    pair,status=sessions.login(command.username,command.password.get_secret_value(),request.client.host if request.client else 'unknown')
    if not pair: raise Failure(status,'UNAUTHORIZED' if status==401 else 'RATE_LIMITED')
    cookie,csrf=pair
    response.set_cookie('pmo_session',cookie,httponly=True,secure=not local,samesite='strict',max_age=8*3600,path='/api/v1')
    response.headers['Cache-Control']='no-store'
    return LoginResult(csrfToken=csrf)

@app.get('/api/v1/auth/me',operation_id='auth_me')
def me(authorization:Auth)->CurrentUser:
    return projects.run(authorization,'auth_me',lambda c,a,r:CurrentUser(accountId=a.account,personId=a.person,orgId=a.org) if a.kind=='human' else deny_agent())

def deny_agent(): raise Failure(403,'FORBIDDEN')

@app.post('/api/v1/auth/logout',operation_id='auth_logout',status_code=204)
def logout(response:Response,authorization:Auth):
    if not isinstance(authorization,Browser): raise Failure(403,'FORBIDDEN')
    projects.run(authorization,'auth_logout',lambda c,a,r:sessions.signout(c,a,r,authorization.cookie))
    response.delete_cookie('pmo_session',path='/api/v1')

@app.exception_handler(Failure)
async def failure(_:Request,e:Failure):
    return JSONResponse({'code':e.code,'message':e.code},status_code=e.status)

@app.get('/api/v1/projects',operation_id='pmo_project_list')
def listing(authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100),stage:str|None=Query(None,alias='filters[stage]'),keyword:str|None=Query(None,alias='filters[keyword]',max_length=160))->Result[Page]:
    return projects.listing(authorization,page,pageSize,stage,keyword)

@app.get('/api/v1/projects/{projectId}',operation_id='pmo_project_get',response_model_exclude_none=True)
def detail(projectId:UUID,authorization:Auth)->Result[ProjectDetail]:
    return projects.detail(authorization,projectId)

@app.post('/api/v1/projects',operation_id='pmo_project_create')
def create(command:CreateProject,key:Annotated[str,Header(alias='Idempotency-Key',min_length=8,max_length=128)],authorization:Auth)->Result[ProjectView]:
    return projects.create(authorization,command,key)

@app.patch('/api/v1/projects/{projectId}',operation_id='pmo_project_draft_update')
def update(projectId:UUID,command:UpdateDraft,key:Annotated[str,Header(alias='Idempotency-Key',min_length=8,max_length=128)],expected:Annotated[str,Header(alias='If-Match',pattern=r'^[1-9][0-9]{0,18}$')],authorization:Auth)->Result[ProjectView]:
    return projects.update_draft(authorization,projectId,int(expected),command,key)
