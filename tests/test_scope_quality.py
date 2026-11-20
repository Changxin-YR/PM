from uuid import uuid4
import sqlalchemy as sa
from pmo import schema_v1 as db
from pmo.scope_quality import metrics
from pmo.requirement_baselines import s as signatures


def test_mixed_weights_do_not_reward_unproven_confirmation_or_removal(live,actor):
    engine,api,_,_=live; token,_=actor(); headers={'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex}
    pid=api.post('/api/v1/projects',headers=headers,json={'code':'QUALITY','name':'Weighted quality'}).json()['data']['projectId']
    with engine.begin() as c:
        person=c.scalar(sa.select(db.projects.c.created_by).where(db.projects.c.id==pid)); baseline=[]
        for priority in ('P0','P1','P2'):
            req=c.scalar(db.requirements.insert().values(project_id=pid,created_by=person,priority=priority,desc='Synthetic scope',acceptance_criteria='Test passes',
                planned_confirm_at='2027-01-02',status='baseline',baseline_version=1).returning(db.requirements.c.id))
            baseline.append(dict(requirementId=str(req),revision=1,priority=priority,desc='Synthetic scope',acceptanceCriteria='Test passes',plannedConfirmAt='2027-01-02'))
        c.execute(signatures.insert().values(project_id=pid,revision=2,signer_role='client_representative',signer_id=person,content_hash='a'*64,snapshot=baseline,rationale='synthetic metric inputs',request_id=uuid4()))
        result,_=metrics(c,pid); assert (result.activeWeight,result.activeCount,result.activeP0)==(6,3,1)
        c.execute(db.requirements.update().where(db.requirements.c.id==req).values(status='customer_confirmed'))
        result,_=metrics(c,pid); assert result.satisfaction is None and result.confirmedWeight==0 and 'CONFIRMATION_RECEIPT_MISSING' in result.gaps
        c.execute(db.requirements.update().where(db.requirements.c.id==req).values(status='cancelled'))
        result,_=metrics(c,pid); assert result.satisfaction is None and result.activeWeight==6 and 'BASELINE_CHAIN_INVALID' in result.gaps
