from uuid import uuid4
import sqlalchemy as sa
from pmo import schema_v1 as db


def test_frozen_baseline_cannot_be_diluted_or_cancelled(live,actor):
    engine,api,_,_=live
    token,_=actor(extra_actions=('pmo_requirement_create','pmo_requirement_list','pmo_requirement_draft_update'))
    def headers(): return {'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex}
    pid=api.post('/api/v1/projects',headers=headers(),json={'name':'需求基线测试','code':'REQ-01'}).json()['data']['projectId']
    path=f'/api/v1/projects/{pid}/requirements'
    key=headers(); body={'desc':'导出全部项目明细','acceptanceCriteria':'表头和行数与原记录相同','priority':'P0','plannedConfirmAt':'2027-01-22'}
    first=api.post(path,headers=key,json=body)
    assert first.status_code==200 and api.post(path,headers=key,json=body).json()==first.json()
    data=first.json()['data']; req=data['requirementId']; item='/api/v1/requirements/'+req
    assert data['acceptanceCriteria']==body['acceptanceCriteria'] and data['plannedConfirmAt']==body['plannedConfirmAt']
    assert api.post(path,headers=key,json={**body,'priority':'P1'}).status_code==409
    assert api.patch(item,headers={**headers(),'If-Match':'1'},json={'fieldsRequirement':{'status':'cancelled'}}).status_code==422
    assert api.get(path,headers=headers(),params={'filters[keyword]':'不存在'}).json()['data']['total']==0
    with engine.begin() as c:
        c.execute(db.requirements.update().where(db.requirements.c.id==req).values(baseline_version=1))
    for response in [api.post(path,headers=headers(),json=body),api.patch(item,headers={**headers(),'If-Match':'1'},json={'fieldsRequirement':{'priority':'P2'}})]:
        assert response.status_code==422 and response.json()['code']=='BASELINE_FROZEN'
    page=api.get(path,headers=headers()).json()['data']
    assert page['total']==1 and page['items'][0]['priority']=='P0' and page['items'][0]['revision']==1
