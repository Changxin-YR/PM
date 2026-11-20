from typing import Annotated
from uuid import UUID, uuid4
import os
from fastapi import FastAPI, Header, Query, Request, Response
from pydantic import Field, SecretStr
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from .request_context import request_id, correlation_id
from .contracts import CreateProject, ProjectView, ProjectDetail, Page, Result, UpdateDraft
from .identity import Browser, Failure
from .contracts import Strict
from . import projects, sessions
from .access import Auth
from .milestone_routes import router as milestone_router
from .requirement_routes import router as requirement_router
from .charter_routes import router as charter_router
from .appointment_routes import router as appointment_router
from .stakeholder_routes import router as stakeholder_router
from .members import router as members_router
from .audits import router as audit_router, is_admin
from .agent_tokens import router as agent_token_router
from .risks import router as risk_router
from .costs import router as cost_router
from .postings import router as posting_router
from .spending import router as spending_router
from .alerts import router as alerts_router

class ErrorResponse(Strict):
    code: str
    message: str
    requestId: UUID


app=FastAPI(title='PMO Lite',version='0.1.0',responses={s:{'model':ErrorResponse} for s in (401,403,404,409,422,429,500)})
app.include_router(milestone_router)
app.include_router(requirement_router)
app.include_router(charter_router)
app.include_router(appointment_router)
app.include_router(stakeholder_router)
app.include_router(members_router)
app.include_router(audit_router)
app.include_router(agent_token_router)
app.include_router(risk_router)
app.include_router(cost_router)
app.include_router(posting_router)
app.include_router(spending_router)
app.include_router(alerts_router)


@app.middleware('http')
async def trace(request: Request, call_next):
    rid=str(uuid4())
    token=request_id.set(rid)
    try:
        response=await call_next(request)
        response.headers['X-Request-ID']=rid
        response.headers['Cache-Control']='no-store'
        return response
    finally:
        request_id.reset(token)


def error(status: int, code: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(code=code,message=code,requestId=UUID(correlation_id())).model_dump(mode='json'),status_code=status)


@app.exception_handler(RequestValidationError)
async def validation_error(_:Request, exc:RequestValidationError):
    # ValidationError contains raw inputs, including passwords and header values.
    return error(422,'VALIDATION_FAILED')


@app.exception_handler(SQLAlchemyError)
async def database_error(_:Request, exc:SQLAlchemyError):
    return error(500,'DATABASE_FAILURE')


@app.exception_handler(StarletteHTTPException)
async def http_error(_:Request, exc:StarletteHTTPException):
    result=error(exc.status_code,{404:'ROUTE_NOT_FOUND',405:'METHOD_NOT_ALLOWED'}.get(exc.status_code,'HTTP_ERROR'))
    if exc.headers and 'Allow' in exc.headers: result.headers['Allow']=exc.headers['Allow']
    return result

class Login(Strict):
    username:str=Field(min_length=1,max_length=120)
    password:SecretStr=Field(min_length=1,max_length=256)

class LoginResult(Strict):
    csrfToken:str

class CurrentUser(Strict):
    accountId:UUID
    personId:UUID
    orgId:UUID
    isSystemAdmin:bool

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
    return projects.run(authorization,'auth_me',lambda c,a,r:CurrentUser(accountId=a.account,personId=a.person,orgId=a.org,isSystemAdmin=is_admin(c,a)) if a.kind=='human' else deny_agent())

def deny_agent(): raise Failure(403,'FORBIDDEN')

@app.post('/api/v1/auth/logout',operation_id='auth_logout',status_code=204)
def logout(response:Response,authorization:Auth):
    if not isinstance(authorization,Browser): raise Failure(403,'FORBIDDEN')
    projects.run(authorization,'auth_logout',lambda c,a,r:sessions.signout(c,a,r,authorization.cookie))
    response.delete_cookie('pmo_session',path='/api/v1')

@app.exception_handler(Failure)
async def failure(_:Request,e:Failure):
    return error(e.status,e.code)

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
