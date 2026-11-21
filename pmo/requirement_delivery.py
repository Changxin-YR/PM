import os
import re
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .evidence import verified_link
from .identity import Failure, visible
from .materials import finish, replay
from .projects import fingerprint, meta, run
from .requirement_additions import addition, applied_addition, initial_signature, origin
from .requirement_baselines import active_member
from .requirements import View, view

router=APIRouter(prefix='/api/v1',tags=['Requirement delivery'])
STAGES=('executing','delivering')
STEPS={'pmo_requirement_deliver':('baseline','delivered'),'human_requirement_accept':('delivered','accepted'),
       'human_customer_confirm':('accepted','customer_confirmed')}


class Proof(Strict):
    evidenceRef: str=Field(pattern=r'^evidence:[0-9a-fA-F-]{36}$')


def can_deliver(c,a,p):
    return p.pm_id==a.person and active_member(c,p,a.person,'pm')


def is_client(c,a,p):
    t=db.stakeholders
    return a.kind=='human' and active_member(c,p,a.person,'client_representative') and bool(c.scalar(sa.select(t.c.id).where(
        t.c.type=='customer',t.c.project_id==p.id,t.c.linked_person_id==a.person).limit(1).with_for_update(read=True)))


def capabilities(c,a,p):
    if a.kind!='human' or p.stage not in STAGES: return None
    actions=[]; pm=can_deliver(c,a,p)
    if pm: actions.append('deliver')
    if pm or active_member(c,p,a.person,'reviewer'): actions.append('accept')
    if is_client(c,a,p): actions.append('customer-confirm' if synthetic_confirmation(c) else 'policy-pending')
    return actions


def synthetic_confirmation(c):
    schema=os.environ.get('PMO_SYNTHETIC_POLICY_SCHEMA','')
    return bool(re.fullmatch('pmo_test_[0-9a-f]{32}',schema) and c.scalar(sa.text('SELECT current_schema()'))==schema)


def baseline_evidence_time(c,pid,old):
    signed=None
    if old.baseline_version==1:
        signed=initial_signature(c,pid)
        if any(i['requirementId']==str(old.id) and i['desc']==old['desc'] and i['priority']==old.priority and i['acceptanceCriteria']==old.acceptance_criteria
                and i.get('plannedConfirmAt')==(old.planned_confirm_at.isoformat() if old.planned_confirm_at else None)
                for i in (signed.snapshot if signed else [])): return None
    if old.baseline_version>=1:
        receipt=c.execute(sa.text('''SELECT ch.before,ch.after,ch.target_type,ch.base_revision,d.old_state,d.new_state,d.decided_at
            FROM change_entries ch JOIN change_decision_records d ON ch.id=d.change_id
            WHERE ch.project_id=:pid AND ch.target_id=:id AND ch.target_type='requirement' AND ch.status='applied' AND d.outcome='applied'
            ORDER BY ch.effective_at DESC,ch.id DESC LIMIT 1'''),dict(pid=pid,id=old.id)).mappings().one_or_none()
        if receipt and (old.baseline_version>1 or (addition(receipt) and applied_addition(receipt,origin(pid,signed)))):
            values=receipt.new_state
            if (values.get('id')==str(old.id) and values.get('project_id')==str(pid)
                    and all(values.get(k)==old[k] for k in ('baseline_version','desc','priority','acceptance_criteria'))
                    and values.get('planned_confirm_at')==(old.planned_confirm_at.isoformat() if old.planned_confirm_at else None)):
                return receipt.decided_at
    raise Failure(422,'SIGNED_BASELINE_REQUIRED')


def advance(auth,op,req,command,key,revision):
    prior_status,next_status=STEPS[op]; sig=fingerprint([str(req),revision,command.evidenceRef])
    def write(c,a,rid):
        p,r,ch=db.projects,db.requirements,db.change_entries
        project=c.execute(sa.select(p).join(r,r.c.project_id==p.c.id).where(r.c.id==req,visible(a)).with_for_update(of=p)).mappings().one_or_none()
        if project is None: raise Failure(404,'RESOURCE_NOT_FOUND')
        if next_status=='customer_confirmed':
            authorized=is_client(c,a,project)
        elif next_status=='accepted': authorized=a.kind=='human' and (can_deliver(c,a,project) or active_member(c,project,a.person,'reviewer'))
        else: authorized=can_deliver(c,a,project)
        if not authorized: raise Failure(403,'FORBIDDEN')
        prior=replay(c,a,op,key,sig,Result[View])
        if prior: return prior
        old=c.execute(sa.select(r).where(r.c.id==req).with_for_update()).mappings().one()
        if old.revision!=revision: raise Failure(409,'VERSION_CONFLICT')
        if project.stage not in STAGES or old.status!=prior_status: raise Failure(422,'INVALID_REQUIREMENT_TRANSITION')
        effective=baseline_evidence_time(c,project.id,old)
        if c.scalar(sa.select(ch.c.id).where(ch.c.project_id==project.id,ch.c.target_type=='requirement',ch.c.target_id==req,
                ch.c.status.not_in(('applied','rejected','withdrawn'))).limit(1)):
            raise Failure(422,'PENDING_SCOPE_CHANGE')
        if next_status=='customer_confirmed':
            if not synthetic_confirmation(c): raise Failure(403,'POLICY_PENDING')
            if c.scalar(sa.text('SELECT EXISTS(SELECT FROM requirement_transitions WHERE requirement_id=:req AND baseline_version=:v AND actor_id=:actor)'),
                        dict(req=req,v=old.baseline_version,actor=a.person)): raise Failure(403,'INDEPENDENT_CUSTOMER_REQUIRED')
            from .requirement_disputes import open_query
            if c.scalar(open_query(old).limit(1)):
                raise Failure(422, 'CUSTOMER_DISPUTE_OPEN', project_id=project.id)
        proof=verified_link(c,a,command.evidenceRef,project.id,'requirement',req)
        if effective and proof.created_at<effective: raise Failure(422,'CURRENT_BASELINE_EVIDENCE_REQUIRED')
        fields=dict(status=next_status,evidence_ref=f'evidence:{proof.id}',revision=revision+1,updated_at=sa.func.now())
        if next_status=='accepted': fields.update(accepted_by=a.person,accepted_at=sa.func.now())
        row=c.execute(r.update().where(r.c.id==req).values(**fields).returning(r)).mappings().one()
        c.execute(sa.text('''INSERT INTO requirement_transitions(requirement_id,revision,baseline_version,status,evidence_id,actor_id,actor_type,request_id)
            VALUES(:req,:rev,:v,:status,:eid,:actor,:kind,:rid)'''),dict(req=req,rev=row.revision,v=row.baseline_version,status=next_status,
            eid=proof.id,actor=a.person,kind=a.kind,rid=UUID(rid)))
        out=view(row)
        return finish(c,a,op,key,rid,project.id,sig,Result[View](data=out,meta=meta(op,[*out.sourceRefs,row.evidence_ref])),fingerprint(view(old).model_dump(mode='json')))
    return run(auth,op,write)


@router.post('/requirements/{requirementId}/deliver',operation_id='pmo_requirement_deliver',response_model_exclude_none=True)
def deliver(requirementId:UUID,command:Proof,authorization:Auth,key:Key,expected:Revision)->Result[View]:
    return advance(authorization,'pmo_requirement_deliver',requirementId,command,key,int(expected))


@router.post('/requirements/{requirementId}/accept',operation_id='human_requirement_accept',response_model_exclude_none=True)
def accept(requirementId:UUID,command:Proof,authorization:Auth,key:Key,expected:Revision)->Result[View]:
    return advance(authorization,'human_requirement_accept',requirementId,command,key,int(expected))


@router.post('/requirements/{requirementId}/customer-confirm',operation_id='human_customer_confirm',response_model_exclude_none=True)
def confirm(requirementId:UUID,command:Proof,authorization:Auth,key:Key,expected:Revision)->Result[View]:
    return advance(authorization,'human_customer_confirm',requirementId,command,key,int(expected))
