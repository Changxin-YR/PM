from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from argon2 import PasswordHasher
from pmo import schema_v1 as db


def test_verified_cost_cycle(live,actor):
    engine,api,port,_=live
    ops=('pmo_cost_posting_propose','pmo_cost_summary','pmo_preapproval_spending_list','human_verify_cost_posting','human_reverse_cost_posting','human_cost_postings_list')
    token,cred=actor(extra_actions=ops)
    def key(rev=1): return {'Idempotency-Key':uuid4().hex,'If-Match':str(rev)}
    bearer={'Authorization':'Bearer '+token}
    proj=api.post('/api/v1/projects',headers={**key(),**bearer},json={'code':'LEDGER','name':'Verification'}).json()['data']
    pid=proj['projectId']; base='projects/'+pid+'/cost-postings'; view='/api/v1/projects/'+pid+'/costs'
    def actual(): return api.get(view,headers=bearer).json()['data']['money']['actual']
    def propose(ref,amount,k=None):
        return api.post('/api/v1/'+base,headers={**(k or key()),**bearer},json={'type':'money','amount':amount,'period':'2020-02','sourceRef':ref})
    assert propose('negative','-1').status_code==422
    k=key(); response=propose('Bill #010','100',k); original=response.json()['data']['postingId']
    assert propose('Bill #010','100',k).json()==response.json() and actual()=='0.00'
    same=propose(' BILL   #010 ','99').json()['data']['postingId']
    proof={'reason':'Checked original document','occurredAt':'2020-02-12T06:45:00Z'}
    loc='cost-postings/'+original+'/'
    assert api.post('/api/v1/'+loc+'verify',headers={**key(),**bearer},json=proof).status_code==403
    assert api.get('/api/v1/'+base,headers=bearer).status_code==403
    with ExitStack() as stack:
        def account(*,as_creator=False,member=True,finance=True):
            secret=token_urlsafe(); name=uuid4().hex; ph=PasswordHasher().hash(secret)
            with engine.begin() as c:
                if as_creator:
                    uid=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==cred))
                    name=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==uid).values(password_hash=ph).returning(db.user_accounts.c.username))
                else:
                    uid=c.scalar(db.user_accounts.insert().values(username=name,password_hash=ph,is_active=True).returning(db.user_accounts.c.id)); person=uuid4()
                    c.execute(db.persons.insert().values(id=person,created_by=person,name='Synthetic',org_id=proj['orgId'],user_id=uid,is_active=True))
                    if member: c.execute(db.project_members.insert().values(project_id=pid,person_id=person,assigned_by=person,role='reviewer',assignment_status='accepted',accepted_at=sa.func.now()))
                c.execute(db.user_roles.insert().values(user_id=uid,role='finance_verifier' if finance else 'system_admin'))
            client=stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/',trust_env=False))
            client.headers['X-CSRF-Token']=client.post('auth/login',json={'username':name,'password':secret}).json()['csrfToken']
            return client
        creator=account(as_creator=True); auditor=account(); second=account(); outside=account(member=False); admin=account(finance=False)
        assert creator.post(loc+'verify',headers=key(),json=proof).json()['code']=='INDEPENDENT_VERIFIER_REQUIRED'
        assert outside.post(loc+'verify',headers=key(),json=proof).status_code==404
        assert admin.post(loc+'verify',headers=key(),json=proof).status_code==403
        for when in (None,'2020-03-01T00:00:00Z'):
            assert auditor.post(loc+'verify',headers=key(),json={**proof,'occurredAt':when}).status_code==422
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda who:who.post(loc+'verify',headers=key(),json=proof),[auditor,second]))
        assert sorted(r.status_code for r in results)==[200,409] and actual()=='100.00'
        assert auditor.post(f'cost-postings/{same}/verify',headers=key(),json=proof).json()['code']=='SOURCE_ALREADY_VERIFIED'
        k=key(2); reason={'reason':'Correct source amount'}
        minus=auditor.post(loc+'reverse',headers=k,json=reason); assert minus.status_code==200,minus.text
        assert auditor.post(loc+'reverse',headers=k,json=reason).json()==minus.json()
        assert second.post(loc+'reverse',headers=key(2),json=reason).json()['code']=='REVERSAL_EXISTS'
        neg=minus.json()['data']; nloc=f'cost-postings/{neg["postingId"]}/verify'
        assert neg['amountSigned']=='-100.00' and actual()=='100.00'
        assert auditor.post(nloc,headers=key(),json=reason).status_code==403
        assert second.post(nloc,headers=key(),json=reason).status_code==200 and actual()=='0.00'
        changed=propose('Amended bill #011','70').json()['data']['postingId']
        changed_proof={**proof,'occurredAt':'2020-02-13T00:00:00Z'}
        k=key(); ok=second.post(f'cost-postings/{changed}/verify',headers=k,json=changed_proof)
        assert ok.status_code==200 and second.post(f'cost-postings/{changed}/verify',headers=k,json=changed_proof).json()==ok.json()
        assert actual()=='70.00'
        ledger=second.get(base,params={'pageSize':2}).json()['data']; assert ledger['total']==4 and len(ledger['items'])==2
        assert outside.get(base).status_code==404
        query='analytics/preapproval-spending'
        before=api.get('/api/v1/'+query,headers=bearer).json()['data']; assert before['total']==3 and all('approvedAt' not in i for i in before['items'])
        assert outside.get(query).json()['data']['total']==0
        with engine.begin() as c:
            fixture=c.scalar(sa.select(db.projects.c.created_by).where(db.projects.c.id==pid))
            for typ,opt,day in [('charter','reject',1),('feasibility','pass',2),('charter','pass',13),('charter','pass',20)]:
                review=c.scalar(db.reviews.insert().values(project_id=pid,type=typ,round=day,status='decided',verdict=opt,created_by=fixture,
                    attendees=[],snapshot_hash='a'*64).returning(db.reviews.c.id))
                c.execute(db.decisions.insert().values(project_id=pid,review_id=review,subject='Synthetic gate decision',option=opt,rationale='Fixture',
                    created_by=fixture,decided_by=fixture,decided_at=datetime(2020,2,day,tzinfo=UTC)))
        pages=[second.get(query,params={'page':i,'pageSize':1}).json() for i in (1,2,3)]
        assert all(p['data']['total']==2 for p in pages) and pages[2]['data']['items']==[]
        matched=[p['data']['items'][0] for p in pages[:2]]; assert {i['postingId'] for i in matched}=={original,neg['postingId']}
        assert all(i['approvedAt'].startswith('2020-02-13') for i in matched) and all(any(ref.startswith('decision:') for ref in p['meta']['sourceRefs']) for p in pages[:2])
    for stmt in (db.cost_postings.update().where(db.cost_postings.c.id==original).values(amount_signed=1),db.cost_postings.delete().where(db.cost_postings.c.id==original)):
        with pytest.raises(sa.exc.DBAPIError),engine.begin() as c: c.execute(stmt)
    with engine.connect() as c:
        rows=c.execute(sa.select(db.cost_postings.c.amount_signed,db.cost_postings.c.verified_by,db.cost_postings.c.created_by).where(db.cost_postings.c.project_id==pid,db.cost_postings.c.status=='verified')).all()
        assert sorted(r.amount_signed for r in rows)==[-100,70,100] and all(r.created_by!=r.verified_by for r in rows)
