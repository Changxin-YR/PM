from datetime import date
from decimal import Decimal
import json
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import Field

from . import schema_v1 as db
from .access import Auth, Key, Revision
from .change_history import Entry, entry
from .change_intents import authority, independent, lookup, snapshot
from .requirement_additions import Fields as AdditionFields, addition
from .change_requests import OPEN, TARGETS, check_frozen, decision_target
from .charters import COLUMNS
from .contracts import Result, Strict
from .identity import Failure, visible
from .materials import finish, replay
from .milestone_forecasts import reconcile
from .policy import guard, matches, require
from .projects import fingerprint, meta, run
from .requirement_delivery import baseline_evidence_time
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
    from .category_budget_changes import apply as apply_categories, categorized

    op='human_change_approve' if approve else 'human_change_reject'; reason=command.rationale.strip(); sig=fingerprint([str(cid),rev,command.rationale])
    def write(c,a,rid):
        ch,p,ar=db.change_entries,db.projects,db.approval_requests
        project=c.execute(sa.select(p).join(ch,ch.c.project_id==p.c.id).where(ch.c.id==cid,visible(a)).with_for_update(of=p)).mappings().one_or_none()
        if not project: raise Failure(404,'RESOURCE_NOT_FOUND')
        i=c.execute(sa.select(ch).where(ch.c.id==cid).with_for_update()).mappings().one()
        grant=authority(c,a,project,i.target_type)
        if not grant or not independent(a,i,None): raise Failure(403,'INDEPENDENT_APPROVER_REQUIRED')
        action=f'change.{i.target_type}'
        guard(c,project,action)
        intent=lookup(c,project.id,cid,lock=True)
        if not independent(a,i,intent): raise Failure(403,'INDEPENDENT_APPROVER_REQUIRED')
        prior=replay(c,a,op,key,sig,Result[Entry])
        if prior: return prior
        if len(reason)<3: raise Failure(422,'INVALID_REASON')
        if rev!=i.revision: raise Failure(409,'VERSION_CONFLICT')
        if i.status!='pending' or project.stage not in OPEN: raise Failure(422,'CHANGE_NOT_PENDING')
        if approve and not matches(c,project,action,i.id): raise Failure(409,'POLICY_SNAPSHOT_STALE',project_id=project.id)
        require(c,project,action,rid)
        category_change = categorized(i)
        if approve and category_change:
            require(c, project, 'cost.category', rid)
            if not matches(c, project, 'cost.category', i.id):
                raise Failure(409, 'POLICY_SNAPSHOT_STALE', project_id=project.id)
        old,frozen = decision_target(c,project,i,lock=True) if approve or not category_change else (None, i.before)
        new_state = None
        refs = []
        at = None
        if approve:
            check_frozen(c,project,i,intent,old,frozen)
            c.execute(ch.update().where(ch.c.id==cid).values(status='approved',approved_by=a.person,revision=rev+1))
            if category_change:
                new_state, refs, at = apply_categories(c, project, a, i)
            else:
                result = apply(c, project, a, i, old)
                new_state = snapshot(result)
                refs.append(f'{i.target_type}:{result.id}@{result.revision}')
                if i.target_type == 'budget':
                    at = result.updated_at
        status='applied' if approve else 'rejected'
        at = at or c.scalar(sa.select(sa.func.clock_timestamp()))
        saved=c.execute(ch.update().where(ch.c.id==cid).values(status=status,revision=rev+(2 if approve else 1),effective_at=at if approve else None,
            updated_at=at).returning(ch)).mappings().one()
        if intent: c.execute(ar.update().where(ar.c.id==cid).values(status='approved' if approve else 'rejected',decided_by=a.person,decided_at=at,reason=reason,updated_at=at))
        c.execute(sa.text('''INSERT INTO change_decision_records(change_id,approver_id,authority_ref,rationale,outcome,old_state,new_state,request_id,decided_at)
            VALUES(:id,:who,:grant,:reason,:outcome,CAST(:old AS jsonb),CAST(:new AS jsonb),:rid,:at)'''),dict(id=cid,who=a.person,grant=grant,reason=reason,
            outcome=status,old=json.dumps(frozen),new=json.dumps(new_state) if new_state is not None else None,rid=UUID(rid),at=at))
        scope_event(c,saved,False)
        refs.insert(0, f'change:{cid}@{saved.revision}')
        receipt=entry(saved).model_copy(update={'decidedBy':a.person,'decisionReason':reason,'decidedAt':at})
        return finish(c,a,op,key,rid,project.id,sig,Result[Entry](data=receipt,meta=meta(op,refs)),
            fingerprint(entry(i).model_dump(mode='json', exclude={'legacySnapshot'})))
    return run(auth,op,write)


@router.post('/changes/{changeId}/approve',operation_id='human_change_approve',response_model_exclude_none=True)
def approve(changeId:UUID,command:Verdict,authorization:Auth,key:Key,expected:Revision)->Result[Entry]:
    return decide(authorization,True,changeId,command,key,int(expected))


@router.post('/changes/{changeId}/reject',operation_id='human_change_reject',response_model_exclude_none=True)
def reject(changeId:UUID,command:Verdict,authorization:Auth,key:Key,expected:Revision)->Result[Entry]:
    return decide(authorization,False,changeId,command,key,int(expected))
