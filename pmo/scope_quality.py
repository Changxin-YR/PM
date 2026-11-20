from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter
from . import schema_v1 as db
from .access import Auth
from .contracts import RULE_VERSION, Result, Strict
from .projects import fingerprint, meta, run
from .requirement_baselines import project, s as signatures

router=APIRouter(prefix='/api/v1',tags=['Quality'])
WEIGHTS={'P0':3,'P1':2,'P2':1}


def scope_event(c,change,opening):
    if change.target_type!='requirement': return
    t=db.rule_events; digest=fingerprint(['LR-05',str(change.id),RULE_VERSION]); refs=[f'requirement:{change.target_id}@{change.base_revision}',f'change:{change.id}@{change.revision}']
    if opening:
        c.execute(t.insert().values(project_id=change.project_id,rule_id='LR-05',rule_version=RULE_VERSION,business_period=str(change.created_at.date()),
            fingerprint=digest,status='open',source_refs=refs))
    else:
        c.execute(t.update().where(t.c.project_id==change.project_id,t.c.rule_id=='LR-05',t.c.fingerprint==digest,t.c.status=='open').values(
            status='resolved',updated_at=sa.func.now(),source_refs=refs))


class Quality(Strict):
    initialCount: int
    activeCount: int
    frozenCount: int
    confirmedWeight: int
    activeWeight: int
    confirmedP0: int
    activeP0: int
    changedInitialCount: int
    approvedChangeCount: int
    satisfaction: str | None=None
    changeRate: str | None=None
    gaps: list[str]


def ratio(n,d): return format(Decimal(n)/Decimal(d),'.6f') if d else None


def metrics(c,pid):
    r,ch=db.requirements,db.change_entries
    signed=c.execute(sa.select(signatures).where(signatures.c.project_id==pid,signatures.c.signer_role=='client_representative').order_by(signatures.c.revision.desc()).limit(1)).mappings().one_or_none()
    initial={i['requirementId']:i for i in signed.snapshot} if signed else {}
    expected={k:(x['priority'],1,False,x['desc'],x['acceptanceCriteria'],x.get('plannedConfirmAt')) for k,x in initial.items()}
    changed=set(); refs=[f'requirement-baseline:{pid}@{signed.revision}'] if signed else []; gaps=set(); approved=0
    decisions=c.execute(sa.text('''SELECT ch.id,ch.target_id,ch.revision,d.new_state FROM change_entries ch JOIN change_decision_records d ON d.change_id=ch.id
        WHERE ch.project_id=:p AND ch.target_type='requirement' AND ch.status='applied' AND d.outcome='applied' ORDER BY d.decided_at,ch.id'''),{'p':pid}).mappings()
    for decision in decisions:
        state=decision.new_state; k=str(decision.target_id); old=expected.get(k)
        if (state['id']!=k or state['project_id']!=str(pid) or not old or old[2] or state['baseline_version']!=old[1]+1):
            gaps.add('BASELINE_CHAIN_INVALID'); continue
        expected[k]=(state['priority'],state['baseline_version'],state['status']=='cancelled',state['desc'],state['acceptance_criteria'],state['planned_confirm_at'])
        changed.add(k); approved+=1; refs.append(f'change:{decision.id}@{decision.revision}')
    pending=set()
    for i in c.execute(sa.select(ch.c.target_id,ch.c.id,ch.c.revision).where(ch.c.project_id==pid,ch.c.target_type=='requirement',ch.c.status.in_(('draft','pending','approved'))).order_by(ch.c.id)):
        pending.add(str(i.target_id)); refs.append(f'change:{i.id}@{i.revision}')
    signoffs={(str(i.requirement_id),i.baseline_version,i.revision) for i in c.execute(sa.text('''SELECT rt.requirement_id,rt.baseline_version,rt.revision FROM requirement_transitions rt
        JOIN requirements r ON r.id=rt.requirement_id WHERE r.project_id=:p AND rt.status='customer_confirmed' AND rt.actor_type='human' '''),{'p':pid})}
    current={str(i.id):i for i in c.execute(sa.select(r).where(r.c.project_id==pid)).mappings()}; active=weight=confirmed=p0=confirmed_p0=frozen=0
    if not initial: gaps.add('INITIAL_BASELINE_MISSING')
    if any(i.baseline_version and k not in expected for k,i in current.items()): gaps.add('BASELINE_CHAIN_INVALID')
    for k,(priority,version,cancelled,desc,criteria,day) in expected.items():
        row=current.get(k); valid=row is not None and (row.priority,row.baseline_version,row.status=='cancelled',row['desc'],row.acceptance_criteria,
            row.planned_confirm_at.isoformat() if row.planned_confirm_at else None)==(priority,version,cancelled,desc,criteria,day)
        if not valid: gaps.add('BASELINE_CHAIN_INVALID')
        if cancelled: continue
        active+=1; w=WEIGHTS[priority]; weight+=w; p0+=priority=='P0'; frozen+=k in pending
        if valid and row.status=='customer_confirmed':
            if (k,version,row.revision) not in signoffs: gaps.add('CONFIRMATION_RECEIPT_MISSING')
            elif k not in pending: confirmed+=w; confirmed_p0+=priority=='P0'
        if row: refs.append(f'requirement:{k}@{row.revision}')
    available=not gaps
    if not active: gaps.add('NO_ACTIVE_REQUIREMENTS')
    return Quality(initialCount=len(initial),activeCount=active,frozenCount=frozen,confirmedWeight=confirmed,activeWeight=weight,confirmedP0=confirmed_p0,activeP0=p0,
        changedInitialCount=len(changed),approvedChangeCount=approved,satisfaction=ratio(confirmed,weight) if available else None,
        changeRate=ratio(len(changed),len(initial)) if available else None,gaps=sorted(gaps)),refs


@router.get('/projects/{projectId}/requirement-metrics',operation_id='human_requirement_metrics',response_model_exclude_none=True)
def get(projectId:UUID,authorization:Auth)->Result[Quality]:
    op='human_requirement_metrics'
    def read(c,a,_):
        project(c,a,projectId,False); result,refs=metrics(c,projectId)
        return Result[Quality](data=result,meta=meta(op,refs))
    return run(authorization,op,read)
