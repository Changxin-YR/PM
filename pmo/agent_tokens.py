from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query, Response
from pydantic import AwareDatetime, Field, StrictBool
from sqlalchemy.dialects.postgresql import insert

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .audits import is_admin
from .contracts import Result, Strict
from .identity import Failure, explicit_grants
from .materials import project_access, replay
from .projects import fingerprint, meta, record_receipt, run
from .tool_routes import ROUTES
from .risks import WRITABLE as RISK_STAGES, can_manage as manages_risks

router=APIRouter(prefix='/api/v1',tags=['Agent credentials'])
t=db.agent_credentials
versions=sa.table('agent_token_versions',sa.column('token_id',sa.Uuid),sa.column('revision',sa.BigInteger))
collections=sa.table('agent_collection_versions',sa.column('account_id',sa.Uuid),sa.column('revision',sa.BigInteger))
events=sa.table('agent_security_events',sa.column('token_id',sa.Uuid),sa.column('actor_id',sa.Uuid),sa.column('action'),sa.column('reason'),sa.column('request_id',sa.Uuid))


class Issue(Strict):
    allowedProjectIds: list[UUID]=Field(max_length=100)
    allowedActions: list[str]=Field(min_length=1,max_length=40)
    expiresAt: AwareDatetime
    allowProjectCreate: StrictBool=False


class Revoke(Strict):
    reason: str=Field(min_length=4,max_length=500,pattern=r'\S')


class Token(Strict):
    tokenId: UUID
    ownerAccountId: UUID
    fingerprint: str
    allowedProjectIds: list[UUID]
    allowedActions: list[str]
    allowProjectCreate: bool
    expiresAt: datetime
    state: Literal['active','expired','revoked','inactive']
    revision: int


class TokenPage(Strict):
    items: list[Token]
    page: int
    pageSize: int
    total: int
    collectionRevision: int
    grantableActions: list[str]


def eligible(c,a):
    r=db.user_roles
    return a.kind=='human' and bool(c.scalar(sa.select(r.c.id).where(r.c.user_id==a.account,r.c.role=='delegator')))


def token_query():
    return sa.select(t.c.id,t.c.delegated_by,t.c.token_fingerprint,t.c.allowed_project_ids,t.c.allowed_actions,
        t.c.allow_project_create,t.c.expires_at,t.c.revoked_at,t.c.status,versions.c.revision).select_from(t.join(versions,t.c.id==versions.c.token_id))


def view(r) -> Token:
    state:Literal['active','expired','revoked','inactive']='revoked' if r.revoked_at or r.status=='revoked' else 'expired' if r.expires_at<=datetime.now(UTC) else 'active' if r.status=='active' else 'inactive'
    return Token(tokenId=r.id,ownerAccountId=r.delegated_by,fingerprint=r.token_fingerprint,allowedProjectIds=r.allowed_project_ids,
        allowedActions=r.allowed_actions,allowProjectCreate=r.allow_project_create,expiresAt=r.expires_at,state=state,revision=r.revision)


def list_tokens(auth,page,size,admin):
    op='admin_agent_tokens_list' if admin else 'human_agent_tokens_list'
    def query(c,a,_):
        if a.kind!='human' or (admin and not is_admin(c,a)): raise Failure(403,'FORBIDDEN')
        conditions=[t.c.org_id==a.org]
        if not admin: conditions.append(t.c.delegated_by==a.account)
        total=c.scalar(sa.select(sa.func.count()).select_from(t).where(*conditions))
        rows=c.execute(token_query().where(*conditions).order_by(t.c.created_at.desc(),t.c.id).limit(size).offset((page-1)*size)).mappings()
        items=[view(r) for r in rows]
        rev=c.scalar(sa.select(collections.c.revision).where(collections.c.account_id==a.account)) or 1
        return Result[TokenPage](data=TokenPage(items=items,page=page,pageSize=size,total=total,collectionRevision=rev,
            grantableActions=sorted(ROUTES) if not admin and eligible(c,a) else []),meta=meta(op,[f'credential:{i.tokenId}@{i.revision}' for i in items]))
    return run(auth,op,query)


@router.get('/me/agent-tokens',operation_id='human_agent_tokens_list')
def personal(authorization: Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100)) -> Result[TokenPage]:
    return list_tokens(authorization,page,pageSize,False)


@router.get('/admin/agents/tokens',operation_id='admin_agent_tokens_list')
def organization(authorization: Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100)) -> Result[TokenPage]:
    return list_tokens(authorization,page,pageSize,True)


def finish(c,a,op,rid,key,sig,tid,reason=None):
    c.execute(events.insert().values(token_id=tid,actor_id=a.account,action='revoke' if reason is not None else 'issue',reason=reason,request_id=UUID(rid)))
    item=view(c.execute(token_query().where(t.c.id==tid)).mappings().one())
    result=Result[Token](data=item,meta=meta(op,[f'credential:{tid}@{item.revision}']))
    body=result.model_dump(mode='json')
    record_receipt(c,a,key,op,rid,tid,None,sig,body,fingerprint(body))
    return result


@router.post('/me/agent-tokens',operation_id='human_agent_token_create')
def issue(command: Issue,response: Response,authorization: Auth,key: Key,expected: Revision) -> Result[Token]:
    op='human_agent_token_create'
    sig=fingerprint([int(expected),command.model_dump(mode='json')])
    def write(c,a,rid):
        if not eligible(c,a): raise Failure(403,'FORBIDDEN')
        actions=set(command.allowedActions); projects=set(command.allowedProjectIds)
        if len(actions)!=len(command.allowedActions) or len(projects)!=len(command.allowedProjectIds): raise Failure(422,'DUPLICATE_SCOPE')
        if not actions.issubset(ROUTES) or command.allowProjectCreate!=('pmo_project_create' in actions) or (command.allowProjectCreate and not a.create):
            raise Failure(403,'INVALID_GRANT')
        for pid in sorted(projects):
            editing=any(ROUTES[action][0]!='GET' and action not in ('pmo_project_create','pmo_risk_create','pmo_risk_update') for action in actions)
            project=project_access(c,a,pid,edit=editing)
            if actions & {'pmo_risk_create','pmo_risk_update'} and (project.stage not in RISK_STAGES or not manages_risks(c,a,project)):
                raise Failure(403,'FORBIDDEN')
            if actions & {'pmo_project_draft_update','pmo_role_assignment_propose'} and (project.initiator_id!=a.person or project.stage!='drafting'):
                raise Failure(403,'FORBIDDEN')
        prior=replay(c,a,op,key,sig,Result[Token])
        if prior: return prior,None
        c.execute(insert(collections).values(account_id=a.account,revision=1).on_conflict_do_nothing())
        version=c.scalar(sa.select(collections.c.revision).where(collections.c.account_id==a.account).with_for_update())
        if version!=int(expected): raise Failure(409,'VERSION_CONFLICT')
        now=datetime.now(UTC)
        if not now<command.expiresAt<=now+timedelta(hours=24): raise Failure(422,'INVALID_EXPIRY')
        if not projects and not command.allowProjectCreate: raise Failure(422,'EMPTY_GRANT')
        secret=token_urlsafe(40); digest=sha256(secret.encode()).hexdigest()
        tid=c.scalar(t.insert().values(delegated_by=a.account,org_id=a.org,allowed_actions=sorted(actions),
            allowed_project_ids=[str(p) for p in sorted(projects)],allow_project_create=command.allowProjectCreate,
            expires_at=command.expiresAt,token_hash=digest,token_fingerprint=digest[:12],status='active').returning(t.c.id))
        if projects: c.execute(explicit_grants.insert(),[dict(credential_id=tid,granted_by=a.account,project_id=p) for p in projects])
        return finish(c,a,op,rid,key,sig,tid),secret
    receipt,secret=run(authorization,op,write)
    if secret: response.headers['X-PMO-New-Token']=secret
    return receipt


def revoke(auth,tid,key,expected,reason,admin):
    op='admin_agent_token_revoke' if admin else 'human_agent_token_revoke'
    sig=fingerprint([str(tid),int(expected),reason])
    def write(c,a,rid):
        if a.kind!='human' or (admin and not is_admin(c,a)): raise Failure(403,'FORBIDDEN')
        cond=[t.c.id==tid,t.c.org_id==a.org]
        if not admin: cond.append(t.c.delegated_by==a.account)
        row=c.execute(token_query().where(*cond).with_for_update(of=t)).mappings().one_or_none()
        if row is None: raise Failure(404,'RESOURCE_NOT_FOUND')
        old=replay(c,a,op,key,sig,Result[Token])
        if old: return old
        if row.revision!=int(expected): raise Failure(409,'VERSION_CONFLICT')
        if row.revoked_at: raise Failure(409,'ALREADY_REVOKED')
        c.execute(t.update().where(t.c.id==tid).values(revoked_at=sa.func.now(),status='revoked'))
        return finish(c,a,op,rid,key,sig,tid,reason)
    return run(auth,op,write)


@router.post('/me/agent-tokens/{tokenId}/revoke',operation_id='human_agent_token_revoke')
def revoke_own(tokenId:UUID,command:Revoke,authorization:Auth,key:Key,expected:Revision)->Result[Token]:
    return revoke(authorization,tokenId,key,expected,command.reason,False)


@router.post('/admin/agents/tokens/{tokenId}/revoke',operation_id='admin_agent_token_revoke')
def revoke_org(tokenId:UUID,command:Revoke,authorization:Auth,key:Key,expected:Revision)->Result[Token]:
    return revoke(authorization,tokenId,key,expected,command.reason,True)
