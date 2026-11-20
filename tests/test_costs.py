from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa
from pmo import schema_v1 as db


def test_cost_snapshot_scope_revision_and_authority(live,actor):
    engine,api,_,_=live
    ops=('pmo_cost_summary','pmo_cost_budget_draft_save','pmo_cost_forecast_save')
    token,_=actor(extra_actions=ops); outside,_=actor(extra_actions=ops)
    def h(rev=1,secret=token):
        return {'Authorization':'Bearer '+secret,'Idempotency-Key':uuid4().hex,'If-Match':str(rev)}
    pid=api.post('/api/v1/projects',headers=h(),json={'code':'COST','name':'Cost versions'}).json()['data']['projectId']
    path=f'/api/v1/projects/{pid}/cost'
    def summary(**params):
        r=api.get(path+'s',headers=h(),params=params); r.raise_for_status(); return r.json()
    assert api.get(path+'s',headers=h(secret=outside)).status_code==404
    body={'type':'money','budget':'125.04'}; key=h()
    first=api.put(path+'-budgets',headers=key,json=body)
    assert first.json()['data']['revision']==2 and api.put(path+'-budgets',headers=key,json=body).json()==first.json()
    assert api.put(path+'-budgets',headers=h(),json=body).status_code==409
    for val in ('0.001','-1','1e5',5):
        assert api.put(path+'-budgets',headers=h(2),json={**body,'budget':val}).status_code==422
    assert summary()['data']['money']['color']=='untrusted'
    body={'type':'money','forecastRemaining':'50','reason':'Estimate updated'}
    assert api.put(path+'-forecasts',headers=h(2),json=body).json()['data']['revision']==3
    with engine.begin() as c:
        person=c.scalar(sa.select(db.projects.c.created_by).where(db.projects.c.id==pid))
        verifier=uuid4()
        c.execute(db.persons.insert().values(id=verifier,created_by=person,org_id=c.scalar(sa.select(db.projects.c.org_id).where(db.projects.c.id==pid)),name='Verifier',is_active=True))
        args=dict(project_id=pid,created_by=person,type='money',category='fixture',period_start=date(2020,11,1),
                  period_end=date(2020,11,30),budget=100,actual=999,forecast_remaining=0,baseline_version=1)
        original=c.scalar(db.cost_entries.insert().values(**args).returning(db.cost_entries.c.id))
        post=dict(project_id=pid,created_by=person,cost_entry_id=original,occurred_at=datetime(2020,11,5,tzinfo=UTC),type='money',
                  period='2020-11',source_type='money',source_ref='synthetic-proof')
        checked=dict(status='verified',verified_at=sa.func.now(),verified_by=verifier,verification_note='Checked fixture')
        positive=c.scalar(db.cost_postings.insert().values(**post,**checked,amount_signed=100,source_fingerprint='1'*64).returning(db.cost_postings.c.id))
        c.execute(db.cost_postings.insert().values(**post,**checked,amount_signed=-100,source_fingerprint='2'*64,posting_of_id=positive,reversal_reason='Correction'))
        c.execute(db.cost_postings.insert().values(**post,**checked,amount_signed=70,source_fingerprint='3'*64))
        c.execute(db.cost_postings.insert().values(**post,amount_signed=555,status='pending'))
    old=summary()['data']; m=old['money']
    assert m=={'budget':'100.00','actual':'70.00','forecastRemaining':'50.00','projectedTotal':'120.00','varianceRate':'0.2000','color':'red'}
    with engine.begin() as c:
        c.execute(db.cost_entries.update().where(db.cost_entries.c.id==original).values(is_current_baseline=False))
        c.execute(db.cost_entries.insert().values(**{**args,'budget':150,'baseline_version':2}))
        c.execute(db.cost_postings.insert().values(**post,**checked,amount_signed=30,source_fingerprint='4'*64))
    current=summary()['data']['money']
    assert current['budget']=='150.00' and current['actual']=='100.00' and current['color']=='green'
    assert summary(asOf=old['asOf'])['data']==old
    assert summary()['data']['effort']['color']=='untrusted'
    assert api.get(path+'s',headers=h(),params={'asOf':'2099-01-01T00:00:00Z'}).status_code==422
    with engine.connect() as c:
        assert c.execute(sa.text('SELECT revision,money_budget,reason FROM cost_plan_versions WHERE project_id=:p ORDER BY revision'),{'p':pid}).all()==[(2,Decimal('125.04'),None),(3,Decimal('125.04'),'Estimate updated')]
