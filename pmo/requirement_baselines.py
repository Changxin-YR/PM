from datetime import date, datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field
from sqlalchemy.dialects.postgresql import JSONB

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .identity import Failure, visible
from .materials import finish, replay
from .projects import fingerprint, meta, run

router=APIRouter(prefix='/api/v1',tags=['Requirement baselines'])
s=sa.table('requirement_baseline_signatures',sa.column('project_id',sa.Uuid),sa.column('revision',sa.BigInteger),
    sa.column('signer_id',sa.Uuid),sa.column('signer_role'),sa.column('content_hash'),sa.column('snapshot',JSONB),
    sa.column('rationale'),sa.column('request_id',sa.Uuid),sa.column('signed_at',sa.DateTime(timezone=True)))
Role=Literal['pm','client_representative']


class Line(Strict):
    requirementId: UUID
    revision: int
    desc: str
    acceptanceCriteria: str
    priority: str
    plannedConfirmAt: date | None=None


class State(Strict):
    projectId: UUID
    revision: int
    contentHash: str
    items: list[Line]
    status: Literal['draft','awaiting_customer','frozen']
    pmSignerId: UUID | None=None
    pmSignedAt: datetime | None=None
    customerSignerId: UUID | None=None
    customerSignedAt: datetime | None=None
    canSignAs: Role | None=None


class Sign(Strict):
    contentHash: str=Field(pattern=r'^[a-f0-9]{64}$')
    role: Role
    reason: str=Field(min_length=4,max_length=2000,pattern=r'\S')


def active_assignment(c,p,person,role):
    m,n,u=db.project_members,db.persons,db.user_accounts
    return c.execute(sa.select(m.c.id,m.c.person_id,m.c.accepted_at).join(n,n.c.id==m.c.person_id).join(u,u.c.id==n.c.user_id).where(
        m.c.project_id==p.id,m.c.person_id==person,m.c.role==role,m.c.assignment_status=='accepted',m.c.accepted_at.is_not(None),
        m.c.revoked_at.is_(None),n.c.org_id==p.org_id,n.c.is_active,u.c.is_active,sa.or_(u.c.locked_until.is_(None),u.c.locked_until<=sa.func.now())).with_for_update(read=True,of=(m,n))).mappings().one_or_none()


def active_member(c,p,person,role):
    return active_assignment(c,p,person,role) is not None


def state(c,a,p):
    latest=c.execute(sa.select(s).where(s.c.project_id==p.id).order_by(s.c.revision.desc()).limit(1)).mappings().one_or_none()
    revision=latest.revision if latest else 1
    frozen=latest if latest and latest.signer_role=='client_representative' else None
    if frozen:
        lines=[Line.model_validate(i) for i in frozen.snapshot]; digest=frozen.content_hash
    else:
        r=db.requirements
        rows=c.execute(sa.select(r).where(r.c.project_id==p.id,r.c.status!='cancelled').order_by(r.c.id)).mappings().all()
        lines=[Line(requirementId=i.id,revision=i.revision,desc=i['desc'],acceptanceCriteria=i.acceptance_criteria,priority=i.priority,
            plannedConfirmAt=i.planned_confirm_at) for i in rows]
        digest=fingerprint([str(p.id),[line.model_dump(mode='json') for line in lines]])
    pm_stmt=sa.select(s).where(s.c.project_id==p.id,s.c.content_hash==digest,s.c.signer_role=='pm')
    if not frozen: pm_stmt=pm_stmt.where(s.c.signer_id==p.pm_id)
    pm=c.execute(pm_stmt.order_by(s.c.revision.desc()).limit(1)).mappings().one_or_none()
    if pm and not frozen and not active_member(c,p,pm.signer_id,'pm'): pm=None
    role=None
    if not frozen and p.stage=='designing' and lines and all(i.baseline_version==0 and i.status=='baseline' and i.planned_confirm_at for i in rows):
        if not pm and a.person==p.pm_id and active_member(c,p,a.person,'pm'): role='pm'
        elif pm and a.person!=pm.signer_id and active_member(c,p,a.person,'client_representative'):
            t=db.stakeholders
            if c.scalar(sa.select(t.c.id).where(t.c.project_id==p.id,t.c.type=='customer',t.c.linked_person_id==a.person).limit(1).with_for_update(read=True)):
                role='client_representative'
    return State(projectId=p.id,revision=revision,contentHash=digest,items=lines,status='frozen' if frozen else 'awaiting_customer' if pm else 'draft',
        pmSignerId=pm.signer_id if pm else None,pmSignedAt=pm.signed_at if pm else None,customerSignerId=frozen.signer_id if frozen else None,
        customerSignedAt=frozen.signed_at if frozen else None,canSignAs=role)


def project(c,a,pid,write):
    if a.kind!='human': raise Failure(403,'FORBIDDEN')
    p=db.projects
    row=c.execute(sa.select(p).where(p.c.id==pid,visible(a)).with_for_update(read=not write)).mappings().one_or_none()
    if row is None: raise Failure(404,'RESOURCE_NOT_FOUND')
    return row


@router.get('/projects/{projectId}/requirement-baseline',operation_id='human_requirement_baseline_get',response_model_exclude_none=True)
def get(projectId:UUID,authorization:Auth)->Result[State]:
    op='human_requirement_baseline_get'
    def read(c,a,_):
        result=state(c,a,project(c,a,projectId,False))
        return Result[State](data=result,meta=meta(op,[f'requirement-baseline:{projectId}@{result.revision}']))
    return run(authorization,op,read)


@router.post('/projects/{projectId}/requirement-baseline/sign',operation_id='human_requirement_baseline_sign',response_model_exclude_none=True)
def sign(projectId:UUID,command:Sign,authorization:Auth,key:Key,expected:Revision)->Result[State]:
    op='human_requirement_baseline_sign'; sig=fingerprint([str(projectId),int(expected),command.model_dump()])
    def write(c,a,rid):
        p=project(c,a,projectId,True); current=state(c,a,p)
        old=replay(c,a,op,key,sig,Result[State])
        if old: return old
        if p.stage!='designing' or current.status=='frozen': raise Failure(422,'BASELINE_LOCKED')
        if current.revision!=int(expected) or command.contentHash!=current.contentHash: raise Failure(409,'BASELINE_CHANGED')
        if any(i.plannedConfirmAt is None for i in current.items): raise Failure(422,'PLANNED_CONFIRM_DATE_REQUIRED')
        if current.canSignAs!=command.role: raise Failure(403,'FORBIDDEN')
        reason=command.reason.strip()
        if len(reason)<4: raise Failure(422,'INVALID_REASON')
        c.execute(s.insert().values(project_id=p.id,revision=current.revision+1,signer_id=a.person,signer_role=command.role,content_hash=current.contentHash,
            snapshot=[i.model_dump(mode='json') for i in current.items],rationale=reason,request_id=UUID(rid)))
        if command.role=='client_representative':
            r=db.requirements
            changed=c.execute(r.update().where(r.c.project_id==p.id,r.c.status=='baseline',r.c.baseline_version==0).values(
                baseline_version=1,revision=r.c.revision+1,updated_at=sa.func.now())).rowcount
            if changed!=len(current.items): raise Failure(409,'BASELINE_CHANGED')
        result=state(c,a,p)
        return finish(c,a,op,key,rid,p.id,sig,Result[State](data=result,meta=meta(op,[f'requirement-baseline:{p.id}@{result.revision}'])),
            fingerprint(current.model_dump(mode='json',exclude_none=True)))
    return run(authorization,op,write)
