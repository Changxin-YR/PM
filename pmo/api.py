from typing import Annotated, Any, Literal
from uuid import UUID, uuid4
from time import perf_counter
import os
from fastapi import FastAPI, Header, Query, Request, Response
from pydantic import Field, SecretStr
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from .request_context import request_id, correlation_id
from .contracts import CreateProject, ProjectView, ProjectDetail, Page, Result, UpdateDraft
from .identity import Browser, Failure, HUMAN_ONLY_OPERATIONS
from .contracts import Strict
from . import projects, sessions
from .access import Auth, DEEP_OBJECT_FILTERS, Filters
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
from .requirement_baselines import router as baseline_router
from .evidence import router as evidence_router
from .requirement_delivery import router as delivery_router
from .requirement_disputes import router as dispute_router
from .change_history import router as change_history_router
from .change_requests import router as change_request_router
from .change_decisions import router as change_decision_router
from .scope_quality import router as scope_quality_router
from .review_routes import router as review_router
from .cost_reconciliation import router as cost_reconciliation_router
from .risk_resolution import router as risk_resolution_router
from .project_lifecycle import router as lifecycle_router
from .retrospectives import router as retrospective_router
from .requirement_exceptions import router as requirement_exception_router
from .reports import router as report_router
from .weekly_drafts import router as weekly_draft_router
from .tasks import router as task_router
from .health import router as health_router
from .facts import router as fact_router
from .report_analysis import router as report_analysis_router
from .org_accounts import router as org_accounts_router
from .org_invitations import router as org_invitations_router
from .assistant import router as assistant_router
from .weekly_health import router as weekly_health_router
from .project_overview import router as project_overview_router
from .health_probes import router as health_probes_router
from .observability import emit
from .tool_routes import ROUTES

class ErrorResponse(Strict):
    code: str
    message: str
    requestId: UUID


class PMOApi(FastAPI):
    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is not None:
            return self.openapi_schema
        schema = super().openapi()
        # Describe recognized actor types; role and resource checks remain in the services.
        for path in schema['paths'].values():
            for route in path.values():
                if not isinstance(route, dict):
                    continue
                operation = route.get('operationId')
                if operation in ROUTES:
                    route['x-actorTypes'] = ['human', 'agent']
                elif operation in HUMAN_ONLY_OPERATIONS:
                    route.update({'x-actorTypes': ['human'], 'x-forbidAgentEvenViaHttp': True})
        return schema


app=PMOApi(title='PMO Lite',version='0.1.0',responses={s:{'model':ErrorResponse} for s in (401,403,404,409,422,429,500)})
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
app.include_router(baseline_router)
app.include_router(evidence_router)
app.include_router(delivery_router)
app.include_router(dispute_router)
app.include_router(change_history_router)
app.include_router(change_request_router)
app.include_router(change_decision_router)
app.include_router(scope_quality_router)
app.include_router(review_router)
app.include_router(cost_reconciliation_router)
app.include_router(risk_resolution_router)
app.include_router(lifecycle_router)
app.include_router(retrospective_router)
app.include_router(requirement_exception_router)
app.include_router(report_router)
app.include_router(weekly_draft_router)
app.include_router(task_router)
app.include_router(health_router)
app.include_router(fact_router)
app.include_router(report_analysis_router)
app.include_router(org_accounts_router)
app.include_router(org_invitations_router)
app.include_router(assistant_router)
app.include_router(weekly_health_router)
app.include_router(project_overview_router)
app.include_router(health_probes_router)


@app.middleware('http')
async def trace(request: Request, call_next):
    rid=str(uuid4())
    token=request_id.set(rid)
    started = perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            emit('http.unhandled', 'error', requestId=rid, exceptionType=type(exc).__name__)
            response = error(500, 'INTERNAL_ERROR')
        response.headers['X-Request-ID']=rid
        response.headers['Cache-Control']='no-store'
        elapsed = round((perf_counter() - started) * 1000, 2)
        status = response.status_code
        operation = getattr(request.scope.get('route'), 'operation_id', None) or 'unmatched'
        if not (operation in ('ops_liveness', 'ops_readiness') and status < 400):
            emit('http.request', 'error' if status >= 500 else 'warning' if elapsed >= 1000 else 'info',
                 requestId=rid, operationId=operation, status=status, durationMs=elapsed, slow=elapsed >= 1000)
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

@app.post('/api/v1/auth/login',operation_id='auth_login',openapi_extra={'x-actorTypes': ['human']})
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

class Logout(Strict):
    pass


class LogoutResult(Strict):
    loggedOut: Literal[True]


@app.post('/api/v1/auth/logout',operation_id='auth_logout')
def logout(response:Response,authorization:Auth,command:Logout|None=None)->Result[LogoutResult]:
    def write(c,a,r):
        if not isinstance(authorization,Browser):
            raise Failure(403,'FORBIDDEN')
        sessions.signout(c,a,r,authorization.cookie)
        return Result[LogoutResult](data=LogoutResult(loggedOut=True),meta=projects.meta('auth_logout',[]))
    result = projects.run(authorization,'auth_logout',write)
    response.delete_cookie('pmo_session',path='/api/v1',httponly=True,samesite='strict')
    return result

@app.exception_handler(Failure)
async def failure(_:Request,e:Failure):
    return error(e.status,e.code)

@app.get('/api/v1/projects',operation_id='pmo_project_list',openapi_extra=DEEP_OBJECT_FILTERS)
def listing(authorization:Auth,filters:Filters,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100))->Result[Page]:
    return projects.listing(authorization,page,pageSize,filters.stage,filters.keyword)

@app.get('/api/v1/projects/{projectId}',operation_id='pmo_project_get',response_model_exclude_none=True)
def detail(projectId:UUID,authorization:Auth)->Result[ProjectDetail]:
    return projects.detail(authorization,projectId)

@app.post('/api/v1/projects',operation_id='pmo_project_create')
def create(command:CreateProject,key:Annotated[str,Header(alias='Idempotency-Key',min_length=8,max_length=128)],authorization:Auth)->Result[ProjectView]:
    return projects.create(authorization,command,key)

@app.patch('/api/v1/projects/{projectId}',operation_id='pmo_project_draft_update')
def update(projectId:UUID,command:UpdateDraft,key:Annotated[str,Header(alias='Idempotency-Key',min_length=8,max_length=128)],expected:Annotated[str,Header(alias='If-Match',pattern=r'^[1-9][0-9]{0,18}$')],authorization:Auth)->Result[ProjectView]:
    return projects.update_draft(authorization,projectId,int(expected),command,key)
