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
from pmo.audit_store import audit
from pmo.projects import fingerprint


@pytest.mark.parametrize('kind', ['money', 'effort'])
def test_verified_cost_cycle(live,actor,kind):
    engine,api,port,_=live
    ops=('pmo_cost_posting_propose','pmo_cost_summary','pmo_preapproval_spending_list','human_verify_cost_posting','human_reverse_cost_posting','human_cost_postings_list')
    token,cred=actor(extra_actions=ops)
    def key(rev=1): return {'Idempotency-Key':uuid4().hex,'If-Match':str(rev)}
    bearer={'Authorization':'Bearer '+token}
    proj=api.post('/api/v1/projects',headers={**key(),**bearer},json={'code':'LEDGER','name':'Verification'}).json()['data']
    pid=proj['projectId']; base='projects/'+pid+'/cost-postings'; view='/api/v1/projects/'+pid+'/costs'
    def actual(): return api.get(view,headers=bearer).json()['data'][kind]['actual']
    def propose(ref,amount,k=None):
        return api.post('/api/v1/'+base,headers={**(k or key()),**bearer},json={'type':kind,'amount':amount,'period':'2020-02','sourceRef':ref})
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
        lost = key()
        with engine.begin() as c:
            c.exec_driver_sql('CREATE TRIGGER cost_outbox_fault BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
        try:
            fault = auditor.post(loc+'verify',headers=lost,json=proof)
            assert (fault.status_code, fault.json()['code']) == (500, 'DATABASE_FAILURE')
            rid = fault.headers['X-Request-ID']
            with engine.connect() as c:
                posting = c.execute(sa.select(db.cost_postings).where(db.cost_postings.c.id == original)).mappings().one()
                assert posting.status == 'pending' and posting.verified_by is None and posting.verified_at is None
                assert c.scalar(sa.select(audit.c.id).where(audit.c.request_id == rid, audit.c.result == 'success')) is None
                assert c.scalar(sa.select(db.outbox_events.c.id).where(db.outbox_events.c.dedupe_key == rid)) is None
                assert c.scalar(sa.select(db.idempotency_keys.c.id).where(
                    db.idempotency_keys.c.idempotency_key == lost['Idempotency-Key'])) is None
            assert actual() == '0.00'
        finally:
            with engine.begin() as c:
                c.exec_driver_sql('DROP TRIGGER cost_outbox_fault ON outbox_events')
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda who:who.post(loc+'verify',headers=lost if who is auditor else key(),json=proof),[auditor,second]))
        assert sorted(r.status_code for r in results)==[200,409] and actual()=='100.00'
        committed, = [result for result in results if result.is_success]
        rid = committed.headers['X-Request-ID']
        with engine.connect() as c:
            trace = c.execute(sa.select(audit).where(audit.c.request_id == rid)).mappings().one()
            assert (trace.action, trace.result) == ('human_verify_cost_posting', 'success')
            assert f'cost-posting:{original}@2' in trace.source_refs and trace.after_hash == fingerprint(committed.json())
            assert c.scalar(sa.select(db.outbox_events.c.event_type).where(db.outbox_events.c.dedupe_key == rid)) == 'human_verify_cost_posting'
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
