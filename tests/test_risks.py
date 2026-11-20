from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import sqlalchemy as sa
from pmo import schema_v1 as db


def test_blocking_risk_cannot_be_downgraded_or_closed_by_edit(live,actor):
    engine,api,_,_=live
    actions=('pmo_risk_create','pmo_risk_list','pmo_risk_update')
    token,_=actor(extra_actions=actions); other,_=actor(extra_actions=actions)
    def h(secret=token): return {'Authorization':'Bearer '+secret,'If-Match':'1','Idempotency-Key':uuid4().hex}
    pid=api.post('/api/v1/projects',headers=h(),json={'code':'RISK','name':'Risk register'}).json()['data']['projectId']
    path=f'/api/v1/projects/{pid}/risks'; body={'statement':'Supplier delay','prob':5,'impact':4,'response':'Follow up','isBlocking':True}
    assert api.post(path,headers=h(),json={**body,'impact':True}).status_code==422
    assert api.get(path,headers=h(other)).status_code==404
    key=h(); first=api.post(path,headers=key,json=body); item=first.json()['data']; loc='/api/v1/risks/'+item['riskId']
    assert item['blockedSince'] and item['ownerId'] and api.post(path,headers=key,json=body).json()==first.json()
    assert api.patch(loc,headers=h(other),json={'fieldsRisk':{'response':'Different'}}).status_code==404
    for bad in ({'isBlocking':False},{'status':'closed'},{'handoffTo':str(uuid4())},{'ownerId':str(uuid4())},{}, {'statement':None}):
        assert api.patch(loc,headers=h(),json={'fieldsRisk':bad}).status_code==422
    with ThreadPoolExecutor(2) as pool:
        writes=list(pool.map(lambda k:api.patch(loc,headers=k,json={'fieldsRisk':{'response':'Inspect again'}}),[h(),h()]))
    assert sorted(w.status_code for w in writes)==[200,409]
    after=api.get(path,headers=h()).json()['data']['items'][0]
    assert after['revision']==2 and after['status']=='open' and after['blockedSince']==item['blockedSince'] and after['isBlocking']
    with engine.begin() as c:
        c.execute(db.risks.update().where(db.risks.c.id==item['riskId']).values(status='closed'))
    locked=api.patch(loc,headers={**h(),'If-Match':'2'},json={'fieldsRisk':{'impact':1}})
    assert locked.status_code==422 and locked.json()['code']=='RISK_REGISTER_LOCKED'
