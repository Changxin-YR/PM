from datetime import date
from decimal import Decimal
import json
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field, ValidationError

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .change_history import Entry
from .change_intents import authority, payload, snapshot
from .change_requests import OPEN, TARGETS, changes, entry
from .charters import COLUMNS
from .contracts import RULE_VERSION, Result, Strict
from .identity import Failure, visible
from .materials import finish, replay
from .milestone_forecasts import reconcile
from .projects import fingerprint, meta, run
from .requirement_baselines import active_member
from .requirement_delivery import baseline_evidence_time, synthetic_confirmation
from .scope_quality import scope_event

router=APIRouter(prefix='/api/v1',tags=['Changes'])


class Verdict(Strict):
    rationale: str=Field(min_length=3,max_length=2000,pattern=r'\S')


def apply(c,p,actor,i,old):
    kind=i.target_type; t=TARGETS[kind]; fields={}; after=i.after
    if kind=='requirement':
        baseline_evidence_time(c,p.id,old)
        keys={'acceptanceCriteria':'acceptance_criteria','plannedConfirmAt':'planned_confirm_at'}
        fields={keys.get(k,k):date.fromisoformat(v) if k=='plannedConfirmAt' else v for k,v in after.items()}
        fields.update(baseline_version=old.baseline_version+1,status=after.get('status','baseline'),accepted_by=None,accepted_at=None,evidence_ref=None)
    elif kind=='milestone':
        keys={'plannedDate':'planned_date','isKey':'is_key'}
        fields={keys.get(k,k):date.fromisoformat(v) if k=='plannedDate' else v for k,v in after.items()}
    elif kind=='charter':
        fields={COLUMNS[k]:Decimal(v) if k in ('roughCost','roughEffort') else v for k,v in after.items()}
    else:
        # One entry version is current. Keep existing posting foreign keys on the original version.
        c.execute(t.update().where(t.c.id==old.id).values(is_current_baseline=False,updated_at=sa.func.now(),revision=old.revision+1))
        values={k:old[k] for k in ('project_id','type','category','period_start','period_end','forecast_remaining')}
        return c.execute(t.insert().values(**values,actual=0,created_by=actor.person,budget=Decimal(after['budget']),baseline_version=old.baseline_version+1,
            source_ref=f'change:{i.id}').returning(t)).mappings().one()
    saved=c.execute(t.update().where(t.c.id==old.id).values(**fields,updated_at=sa.func.now(),revision=old.revision+1).returning(t)).mappings().one()
    if kind=='milestone': reconcile(c,p,saved)
    return saved


def decide(auth,approve,cid,command,key,rev):
    op='human_change_approve' if approve else 'human_change_reject'; reason=command.rationale.strip(); sig=fingerprint([str(cid),rev,command.rationale])
    def write(c,a,rid):
        ch,p,ar=db.change_entries,db.projects,db.approval_requests
        project=c.execute(sa.select(p).join(ch,ch.c.project_id==p.c.id).where(ch.c.id==cid,visible(a)).with_for_update(of=p)).mappings().one_or_none()
        if not project: raise Failure(404,'RESOURCE_NOT_FOUND')
        i=c.execute(sa.select(ch).where(ch.c.id==cid).with_for_update()).mappings().one()
        grant=authority(c,a,project,i.target_type)
        if not grant or i.requested_by==a.person: raise Failure(403,'INDEPENDENT_APPROVER_REQUIRED')
        if not synthetic_confirmation(c): raise Failure(403,'POLICY_PENDING')
        prior=replay(c,a,op,key,sig,Result[Entry])
        if prior: return prior
        if len(reason)<3: raise Failure(422,'INVALID_REASON')
        if rev!=i.revision: raise Failure(409,'VERSION_CONFLICT')
        if i.status!='pending' or project.stage not in OPEN: raise Failure(422,'CHANGE_NOT_PENDING')
        intent=c.execute(sa.select(ar).where(ar.c.id==cid,ar.c.project_id==project.id,ar.c.action=='baseline_change').with_for_update()).mappings().one_or_none()
        if intent and (intent.delegated_by==a.person or intent.requested_by==a.person): raise Failure(403,'INDEPENDENT_APPROVER_REQUIRED')
        table=TARGETS[i.target_type]; old=c.execute(sa.select(table).where(table.c.id==i.target_id,table.c.project_id==project.id).with_for_update()).mappings().one_or_none()
        if not old: raise Failure(409,'APPROVAL_STALE')
        result=None
        if approve:
            if (not intent or intent.status!='pending' or intent.rule_version!=RULE_VERSION or intent.expected_revision!=old.revision or i.base_revision!=old.revision
                or intent.payload_hash!=payload(i) or intent.snapshot_hash!=fingerprint(snapshot(old)) or intent.requested_by!=project.pm_id
                or not active_member(c,project,i.requested_by,'pm')): raise Failure(409,'APPROVAL_STALE')
            try: before,after=changes(i.target_type,old,i.after)
            except ValidationError: raise Failure(409,'APPROVAL_STALE') from None
            if fingerprint(before)!=fingerprint(i.before) or fingerprint(after)!=fingerprint(i.after): raise Failure(409,'APPROVAL_STALE')
            c.execute(ch.update().where(ch.c.id==cid).values(status='approved',approved_by=a.person,revision=rev+1))
            result=apply(c,project,a,i,old)
        status='applied' if approve else 'rejected'
        at=c.scalar(sa.select(sa.func.clock_timestamp()))
        saved=c.execute(ch.update().where(ch.c.id==cid).values(status=status,revision=rev+(2 if approve else 1),effective_at=at if approve else None,
            updated_at=at).returning(ch)).mappings().one()
        if intent: c.execute(ar.update().where(ar.c.id==cid).values(status='approved' if approve else 'rejected',decided_by=a.person,decided_at=at,reason=reason,updated_at=at))
        c.execute(sa.text('''INSERT INTO change_decision_records(change_id,approver_id,authority_ref,rationale,outcome,old_state,new_state,request_id,decided_at)
            VALUES(:id,:who,:grant,:reason,:outcome,CAST(:old AS jsonb),CAST(:new AS jsonb),:rid,:at)'''),dict(id=cid,who=a.person,grant=grant,reason=reason,
            outcome=status,old=json.dumps(snapshot(old)),new=json.dumps(snapshot(result)) if result else None,rid=UUID(rid),at=at))
        scope_event(c,saved,False); refs=[f'change:{cid}@{saved.revision}']
        if result: refs.append(f'{i.target_type}:{result.id}@{result.revision}')
        receipt=entry(saved).model_copy(update={'decidedBy':a.person,'decisionReason':reason,'decidedAt':at})
        return finish(c,a,op,key,rid,project.id,sig,Result[Entry](data=receipt,meta=meta(op,refs)),fingerprint(entry(i).model_dump(mode='json')))
    return run(auth,op,write)


@router.post('/changes/{changeId}/approve',operation_id='human_change_approve',response_model_exclude_none=True)
def approve(changeId:UUID,command:Verdict,authorization:Auth,key:Key,expected:Revision)->Result[Entry]:
    return decide(authorization,True,changeId,command,key,int(expected))


@router.post('/changes/{changeId}/reject',operation_id='human_change_reject',response_model_exclude_none=True)
def reject(changeId:UUID,command:Verdict,authorization:Auth,key:Key,expected:Revision)->Result[Entry]:
    return decide(authorization,False,changeId,command,key,int(expected))
