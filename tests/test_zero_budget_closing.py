from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from pmo import schema_v1 as db
from test_lifecycle import close_materials, complete_milestones, execute, execution_fixture, gate, propose, stage
from test_reviews import data, decide, headers, team as team


def gaps(pm, project):
    return {gap['code'] for gap in data(pm.get(project+'/closing/check'))['missing']}


def reconcile(who, project, source):
    path=project+'/cost-reconciliations'
    record=data(who.get(path))
    return data(who.post(path,headers=headers(record['revision']),json={'costHash':record['costHash'],
        'evidenceRefs':[source['evidenceRef']]}))


def test_zero_effort_closes_only_with_bound_declaration_and_independent_actuals(live, team):
    _, _, users, _ = team
    pm, reviewer, customer, reconciler = (users[k] for k in ('pm','reviewer','client_representative','outsider'))
    project, req, risk = execution_fixture(live,team,effort_budget='0')
    complete_milestones(pm,reviewer,project)
    data(execute(pm,project,data(propose(pm,project,'delivering'))))
    _, risk_proof, source = close_materials(pm,reviewer,customer,project,req,risk,actuals=('100.00','0.00'))
    data(reviewer.post(f'risks/{risk}/close',headers=headers(1),json={'resolutionEvidenceRef':risk_proof['evidenceRef']}))
    missing=gaps(pm,project)
    assert missing=={'EFFORT_CLOSING_BUDGET_UNVERIFIED','COST_RECONCILIATION_REQUIRED'}
    health=data(pm.get(project+'/health'))
    claim=data(pm.post(project+'/health/zero-budget',headers=headers(health['projectRevision']),json={
        'type':'effort','baselineHash':health['zeroBudgetHashes']['effort'],
        'reason':'The signed synthetic project performs no chargeable person-day work.'}))
    assert gaps(pm,project)=={'COST_RECONCILIATION_REQUIRED'}
    bill=reconcile(reconciler,project,source)
    assert not gaps(pm,project)
    forecast=project+'/cost-forecasts'
    plan=data(pm.get(project+'/costs'))['plan']['revision']
    for rev,amount in [(plan,'0.01'),(plan+1,'0.00')]:
        data(pm.put(forecast,headers=headers(rev),json={'type':'effort','forecastRemaining':amount,
            'reason':'Update the independent remaining-work estimate for the synthetic case.'}))
        if amount=='0.01':
            assert {'EFFORT_FORECAST_NOT_ZERO','COST_RECONCILIATION_REQUIRED'} <= gaps(pm,project)
    new_bill=reconcile(reconciler,project,source)
    assert bill['receiptId']!=new_bill['receiptId']
    review=gate(pm,project,'closing')
    engine=live[0]
    with engine.connect() as c:
        snapshot=c.scalar(sa.text('SELECT source_refs FROM review_submission_snapshots WHERE review_id=:id'),
            {'id':UUID(review.split('/')[-1])})
    assert f"zero-budget:{claim['declarationId']}" in snapshot
    assert data(decide(reviewer,review))['decision']['stage']=='closed'
    assert stage(pm,project)['stage']=='closed'


def test_verified_nonzero_effort_never_uses_zero_declaration_to_close(live, team):
    _, _, users, _ = team
    pm, reviewer, customer = (users[k] for k in ('pm','reviewer','client_representative'))
    project, req, risk = execution_fixture(live,team,effort_budget='0')
    health=data(pm.get(project+'/health'))
    data(pm.post(project+'/health/zero-budget',headers=headers(health['projectRevision']),json={
        'type':'effort','baselineHash':health['zeroBudgetHashes']['effort'],'reason':'Baseline explicitly zero in the synthetic fixture.'}))
    now=datetime.now(UTC)
    item=data(pm.post(project+'/cost-postings',headers=headers(),json={'type':'effort','amount':'0.01',
        'period':now.strftime('%Y-%m'),'sourceRef':'Unexpected synthetic labor line'}))
    data(reviewer.post(f"cost-postings/{item['postingId']}/verify",headers=headers(1),json={
        'reason':'Independently checked synthetic source','occurredAt':now.isoformat()}))
    complete_milestones(pm,reviewer,project)
    data(execute(pm,project,data(propose(pm,project,'delivering'))))
    assert 'EFFORT_CLOSING_VARIANCE' in gaps(pm,project)
    retained=data(pm.get(project+'/costs'))['effort']['actual']
    assert retained=='0.01'
