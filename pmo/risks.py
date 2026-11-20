from datetime import datetime
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import Field, StrictBool, model_validator

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .identity import Failure, visible
from .materials import finish, project_access, replay
from .projects import fingerprint, meta, run

router=APIRouter(prefix='/api/v1',tags=['Risks'])
r=db.risks
Statement=Annotated[str,Field(min_length=5,max_length=3000,pattern=r'\S')]
Rating=Annotated[int,Field(strict=True,ge=1,le=5)]
ResponseText=Annotated[str,Field(max_length=3000)]
WRITABLE=('drafting','designing','executing','on_hold','delivering')


class Create(Strict):
    statement: Statement
    prob: Rating
    impact: Rating
    response: ResponseText
    isBlocking: StrictBool=False


class Fields(Strict):
    statement: Statement | None=None
    prob: Rating | None=None
    impact: Rating | None=None
    response: ResponseText | None=None

    @model_validator(mode='after')
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self,k) is None for k in self.model_fields_set):
            raise ValueError('Expected one or more non-null risk fields')
        return self


class Update(Strict):
    fieldsRisk: Fields


class Risk(Strict):
    riskId: UUID
    projectId: UUID
    ownerId: UUID | None
    statement: str
    prob: Rating
    impact: Rating
    response: str
    isBlocking: bool
    blockedSince: datetime | None
    status: str
    revision: int
    canEdit: bool | None=None


class Page(Strict):
    items: list[Risk]
    total: int
    page: int
    pageSize: int
    canCreate: bool | None=None


def view(row) -> Risk:
    return Risk(riskId=row.id,projectId=row.project_id,ownerId=row.owner_id,statement=row.statement,prob=row.prob,
        impact=row.impact,response=row.response,isBlocking=row.is_blocking,blockedSince=row.blocked_since,status=row.status,revision=row.revision)


def can_manage(c,a,p) -> bool:
    m=db.project_members.c
    role='initiator' if p.stage=='drafting' and p.initiator_id==a.person else 'pm'
    if role=='pm' and p.pm_id!=a.person: return False
    return bool(c.scalar(sa.select(m.id).where(m.project_id==p.id,m.person_id==a.person,m.role==role,
        m.assignment_status=='accepted',m.accepted_at.is_not(None),m.revoked_at.is_(None))))


@router.get('/projects/{projectId}/risks',operation_id='pmo_risk_list',response_model_exclude_none=True)
def listing(projectId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100))->Result[Page]:
    op='pmo_risk_list'
    def query(c,a,_):
        project=project_access(c,a,projectId)
        cond=r.c.project_id==projectId
        total=c.scalar(sa.select(sa.func.count()).select_from(r).where(cond))
        rows=c.execute(sa.select(r).where(cond).order_by(r.c.is_blocking.desc(),r.c.created_at.desc(),r.c.id).offset((page-1)*pageSize).limit(pageSize)).mappings()
        items=[view(row) for row in rows]
        manage=project.stage in WRITABLE and can_manage(c,a,project) if a.kind=='human' else None
        if a.kind=='human':
            for item in items: item.canEdit=project.stage in WRITABLE and item.status=='open' and (bool(manage) or item.ownerId==a.person)
        return Result[Page](data=Page(items=items,total=total,page=page,pageSize=pageSize,canCreate=manage),meta=meta(op,[f'risk:{i.riskId}@{i.revision}' for i in items]))
    return run(authorization,op,query)


def save(auth,key,command,*,project_id=None,risk_id=None,expected=None):
    creating=risk_id is None
    op='pmo_risk_create' if creating else 'pmo_risk_update'
    sig=fingerprint([str(project_id if creating else risk_id),expected,command.model_dump(mode='json',exclude_unset=True)])
    def write(c,a,rid):
        p=db.projects
        pid=project_id if creating else c.scalar(sa.select(r.c.project_id).join(p,p.c.id==r.c.project_id).where(r.c.id==risk_id,visible(a)))
        project=c.execute(sa.select(p).where(p.c.id==pid,visible(a)).with_for_update()).mappings().one_or_none()
        if project is None: raise Failure(404,'RESOURCE_NOT_FOUND')
        if project.stage not in WRITABLE: raise Failure(422,'RISK_REGISTER_LOCKED')
        row=None if creating else c.execute(sa.select(r).where(r.c.id==risk_id,r.c.project_id==pid).with_for_update()).mappings().one()
        if not can_manage(c,a,project) and (row is None or row.owner_id!=a.person): raise Failure(403,'FORBIDDEN')
        old=replay(c,a,op,key,sig,Result[Risk])
        if old: return old
        before=None
        if creating:
            stmt=r.insert().values(project_id=pid,created_by=a.person,owner_id=a.person,statement=command.statement,
                prob=command.prob,impact=command.impact,response=command.response,is_blocking=command.isBlocking,
                blocked_since=sa.func.now() if command.isBlocking else None,status='open')
        else:
            if row.revision!=expected: raise Failure(409,'VERSION_CONFLICT')
            if row.status!='open': raise Failure(422,'RISK_REGISTER_LOCKED')
            before=fingerprint(view(row).model_dump(mode='json'))
            stmt=r.update().where(r.c.id==risk_id).values(**command.fieldsRisk.model_dump(exclude_unset=True),revision=expected+1,updated_at=sa.func.now())
        item=view(c.execute(stmt.returning(r)).mappings().one())
        return finish(c,a,op,key,rid,pid,sig,Result[Risk](data=item,meta=meta(op,[f'risk:{item.riskId}@{item.revision}'])),before)
    return run(auth,op,write)


@router.post('/projects/{projectId}/risks',operation_id='pmo_risk_create',response_model_exclude_none=True)
def create(projectId:UUID,command:Create,authorization:Auth,key:Key)->Result[Risk]:
    return save(authorization,key,command,project_id=projectId)


@router.patch('/risks/{riskId}',operation_id='pmo_risk_update',response_model_exclude_none=True)
def update(riskId:UUID,command:Update,authorization:Auth,key:Key,expected:Revision)->Result[Risk]:
    return save(authorization,key,command,risk_id=riskId,expected=int(expected))
