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
from .requirement_additions import Fields as AdditionFields, addition, initial_signature, origin, valid_origin
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
    if addition(i):
        f=AdditionFields.model_validate(after)
        return c.execute(t.insert().values(id=i.target_id,project_id=p.id,created_by=i.requested_by,desc=f.desc,
            acceptance_criteria=f.acceptanceCriteria,priority=f.priority,planned_confirm_at=f.plannedConfirmAt,
            status='baseline',baseline_version=1).returning(t)).mappings().one()
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
        # History uses the statement's timestamp. Retire and insert in the same
        # statement without moving posting foreign keys off the original row.
        carry = ('project_id', 'type', 'category', 'period_start', 'period_end', 'forecast_remaining')
        cutover = sa.func.statement_timestamp()
        retired = t.update().where(t.c.id == old.id).values(is_current_baseline=False, updated_at=cutover,
            revision=t.c.revision + 1).returning(*(t.c[k] for k in carry), t.c.baseline_version).cte('retired_budget')
        replacement = sa.select(*(retired.c[k] for k in carry), sa.literal(0), sa.literal(actor.person),
            sa.literal(Decimal(after['budget'])), retired.c.baseline_version + 1, sa.literal(f'change:{i.id}'), cutover, cutover)
        return c.execute(t.insert().from_select((*carry, 'actual', 'created_by', 'budget', 'baseline_version',
            'source_ref', 'created_at', 'updated_at'), replacement).returning(t)).mappings().one()
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
        added=addition(i)
        if (added and old is not None) or (not added and old is None): raise Failure(409,'APPROVAL_STALE')
        frozen=i.before if added else snapshot(old)
        result=None
        if approve:
            if (not intent or intent.status!='pending' or intent.rule_version!=RULE_VERSION or intent.expected_revision!=i.base_revision
                or intent.payload_hash!=payload(i) or intent.snapshot_hash!=fingerprint(frozen) or intent.requested_by!=project.pm_id
                or not active_member(c,project,i.requested_by,'pm')): raise Failure(409,'APPROVAL_STALE')
            try:
                if added:
                    stamp=origin(project.id,initial_signature(c,project.id))
                    if not valid_origin(i.before,stamp) or i.base_revision!=stamp['initialBaselineRevision']:
                        raise Failure(409,'APPROVAL_STALE')
                    before,after=stamp,AdditionFields.model_validate(i.after).model_dump(mode='json')
                else:
                    if i.base_revision!=old.revision: raise Failure(409,'APPROVAL_STALE')
                    before,after=changes(i.target_type,old,i.after)
            except ValidationError: raise Failure(409,'APPROVAL_STALE') from None
            if fingerprint(before)!=fingerprint(i.before) or fingerprint(after)!=fingerprint(i.after): raise Failure(409,'APPROVAL_STALE')
            c.execute(ch.update().where(ch.c.id==cid).values(status='approved',approved_by=a.person,revision=rev+1))
            result=apply(c,project,a,i,old)
        status='applied' if approve else 'rejected'
        at=result.updated_at if result is not None and i.target_type=='budget' else c.scalar(sa.select(sa.func.clock_timestamp()))
        saved=c.execute(ch.update().where(ch.c.id==cid).values(status=status,revision=rev+(2 if approve else 1),effective_at=at if approve else None,
            updated_at=at).returning(ch)).mappings().one()
        if intent: c.execute(ar.update().where(ar.c.id==cid).values(status='approved' if approve else 'rejected',decided_by=a.person,decided_at=at,reason=reason,updated_at=at))
        c.execute(sa.text('''INSERT INTO change_decision_records(change_id,approver_id,authority_ref,rationale,outcome,old_state,new_state,request_id,decided_at)
            VALUES(:id,:who,:grant,:reason,:outcome,CAST(:old AS jsonb),CAST(:new AS jsonb),:rid,:at)'''),dict(id=cid,who=a.person,grant=grant,reason=reason,
            outcome=status,old=json.dumps(frozen),new=json.dumps(snapshot(result)) if result else None,rid=UUID(rid),at=at))
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
