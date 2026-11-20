from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import Field, ValidationError

from . import charter_models, milestones, requirements, schema_v1 as db
from .access import Auth, Key, Revision
from .change_history import Entry, Page, with_decision
from .change_intents import authority, capture
from .contracts import Result, Strict
from .costs import Amount
from .identity import Failure, visible
from .materials import finish, replay
from .materials import project_access
from .projects import fingerprint, meta, run
from .requirement_delivery import can_deliver

router=APIRouter(prefix='/api/v1',tags=['Changes'])
OPEN=('designing','executing','on_hold','delivering')
TARGETS={'requirement':db.requirements,'milestone':db.milestones,'budget':db.cost_entries,'charter':db.charters}


class Proposal(Strict):
    targetType: Literal['requirement','milestone','budget','charter']
    targetId: UUID
    before: dict=Field(min_length=1,max_length=10)
    after: dict=Field(min_length=1,max_length=10)
    reason: str=Field(min_length=3,max_length=2000,pattern=r'\S')


class Budget(Strict):
    budget: Amount


class Ledger(Page):
    canPropose: bool
    decidableIds: list[UUID]
    policyPending: bool


def entry(i):
    return Entry(changeId=i.id,projectId=i.project_id,targetType=i.target_type,targetId=i.target_id,before=i.before,after=i.after,status=i.status,
        reason=i.reason,baseRevision=i.base_revision,revision=i.revision,requestedBy=i.requested_by,approvedBy=i.approved_by,createdAt=i.created_at,effectiveAt=i.effective_at,
        decidedBy=i.get('approver_id'),decisionReason=i.get('rationale'),decidedAt=i.get('decided_at'))


@router.get('/projects/{projectId}/changes',operation_id='human_changes_list',response_model_exclude_none=True)
def ledger(projectId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100))->Result[Ledger]:
    op='human_changes_list'
    def read(c,a,_):
        p=project_access(c,a,projectId); t=db.change_entries
        targets=[sa.and_(t.c.target_type==kind,sa.exists(sa.select(1).select_from(table).where(table.c.id==t.c.target_id,table.c.project_id==projectId))) for kind,table in TARGETS.items()]
        matched=with_decision(t).where(t.c.project_id==projectId,sa.or_(*targets)).cte('matched')
        total=sa.select(sa.func.count().label('total')).select_from(matched).subquery()
        rows=sa.select(matched).order_by(matched.c.created_at.desc(),matched.c.id.desc()).offset((page-1)*pageSize).limit(pageSize).subquery()
        records=c.execute(sa.select(total.c.total,rows).select_from(total.outerjoin(rows,sa.true())).order_by(rows.c.created_at.desc(),rows.c.id.desc())).mappings().all()
        items=[entry(r) for r in records if r.id]; info=meta(op,[f'change:{i.changeId}@{i.revision}' for i in items])
        from .requirement_delivery import synthetic_confirmation
        scopes={kind for kind in {i.targetType for i in items if i.status=='pending' and i.requestedBy!=a.person} if authority(c,a,p,kind)}
        return Result[Ledger](data=Ledger(items=items,page=page,pageSize=pageSize,total=records[0].total,asOf=info.asOf,canPropose=p.stage in OPEN and can_deliver(c,a,p),
            decidableIds=[i.changeId for i in items if i.targetType in scopes and i.status=='pending' and i.requestedBy!=a.person and p.stage in OPEN],policyPending=not synthetic_confirmation(c)),meta=info)
    return run(authorization,op,read)


def changes(kind,row,after):
    if kind=='requirement':
        if not row.baseline_version or row.status=='cancelled': raise Failure(422,'NO_ACTIVE_BASELINE')
        current=requirements.view(row).model_dump(mode='json')
        if after=={'status':'cancelled'}: return {'status':row.status},after
        data=requirements.Fields.model_validate(after)
    elif kind=='milestone':
        if row.actual_date or row.is_cancelled: raise Failure(422,'MILESTONE_LOCKED')
        current=milestones.view(row).model_dump(mode='json')
        if set(after)-{'plannedDate','name','isKey'}: raise Failure(422,'INVALID_CHANGE_FIELDS')
        data=milestones.Fields.model_validate(after)
    elif kind=='budget':
        if not row.is_current_baseline: raise Failure(422,'NO_ACTIVE_BASELINE')
        current={'budget':format(row.budget,'.2f')}; amount=Budget.model_validate(after)
        from decimal import Decimal
        return current,{'budget':format(Decimal(amount.budget),'.2f')}
    else:
        if not row.is_frozen: raise Failure(422,'NO_ACTIVE_BASELINE')
        current=charter_models.full(row).model_dump(mode='json'); data=charter_models.Fields.model_validate(after)
        if 'roughDuration' in after: current['roughDuration']=current['roughDurationDays']
    return {k:current[k] for k in after},data.model_dump(mode='json',exclude_unset=True)


@router.post('/projects/{projectId}/changes',operation_id='pmo_change_propose',response_model_exclude_none=True)
def propose(projectId:UUID,command:Proposal,authorization:Auth,key:Key,expected:Revision)->Result[Entry]:
    op='pmo_change_propose'; sig=fingerprint([str(projectId),int(expected),command.model_dump(mode='json')])
    def write(c,a,rid):
        p,t,ch=db.projects,TARGETS[command.targetType],db.change_entries
        project=c.execute(sa.select(p).where(p.c.id==projectId,visible(a)).with_for_update()).mappings().one_or_none()
        if not project: raise Failure(404,'RESOURCE_NOT_FOUND')
        if not can_deliver(c,a,project): raise Failure(403,'FORBIDDEN')
        prior=replay(c,a,op,key,sig,Result[Entry])
        if prior: return prior
        if project.stage not in OPEN or (command.targetType=='milestone' and project.stage=='designing'): raise Failure(422,'NO_ACTIVE_BASELINE')
        row=c.execute(sa.select(t).where(t.c.id==command.targetId,t.c.project_id==projectId).with_for_update()).mappings().one_or_none()
        if not row: raise Failure(404,'RESOURCE_NOT_FOUND')
        if row.revision!=int(expected): raise Failure(409,'VERSION_CONFLICT')
        try: before,after=changes(command.targetType,row,command.after)
        except ValidationError: raise Failure(422,'INVALID_CHANGE_FIELDS') from None
        if fingerprint(before)!=fingerprint(command.before): raise Failure(409,'BASELINE_CHANGED')
        if fingerprint(before)==fingerprint(after) or len(command.reason.strip())<3: raise Failure(422,'NO_CHANGE')
        if c.scalar(sa.select(ch.c.id).where(ch.c.project_id==projectId,ch.c.target_id==row.id,ch.c.target_type==command.targetType,
                ch.c.status.in_(('draft','pending','approved'))).limit(1)): raise Failure(409,'CHANGE_ALREADY_PENDING')
        i=c.execute(ch.insert().values(project_id=projectId,target_id=row.id,target_type=command.targetType,before=before,after=after,
            base_revision=row.revision,reason=command.reason.strip(),status='pending',requested_by=a.person,created_by=a.person).returning(ch)).mappings().one()
        capture(c,a,i,row); result=entry(i)
        return finish(c,a,op,key,rid,projectId,sig,Result[Entry](data=result,meta=meta(op,[f'change:{i.id}@{i.revision}'])))
    return run(authorization,op,write)
