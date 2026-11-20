from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
import sqlalchemy as sa
from pmo import schema_v1 as db


def test_charter_history_conflicts_freeze_and_human_boundary(live,actor):
    engine,api,_,_=live
    token,_=actor(extra_actions=('pmo_charter_save','human_charter_get'))
    def headers(revision=1): return {'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex,'If-Match':str(revision)}
    pid=api.post('/api/v1/projects',headers=headers(),json={'code':'CH-01','name':'Charter'}).json()['data']['projectId']
    path=f'/api/v1/projects/{pid}/charter'
    assert api.get(path,headers=headers()).status_code==403
    body={'fieldsCharter':{'valueProp':'Initial','roughCost':'100.01','roughEffort':'0.00','topRisks':[{'statement':'Delay','response':'Review'}]}}
    bad=api.put(path,headers=headers(),json={'fieldsCharter':{'roughCost':'99999999999999999.99'}})
    assert bad.status_code==422
    with ThreadPoolExecutor(2) as pool:
        calls=list(pool.map(lambda h:(h,api.put(path,headers=h,json=body)),[headers(),headers()]))
    assert sorted(r.status_code for _,r in calls)==[200,409]
    key,success=next(entry for entry in calls if entry[1].is_success)
    assert api.put(path,headers=key,json=body).json()==success.json()
    first=success.json()['data']; cid=first['charterId']; assert first['revision']==2
    modified=api.put(path,headers=headers(2),json={'fieldsCharter':{'valueProp':'Updated'}}).json()['data']
    assert modified['revision']==3 and modified['roughEffort']=='0.00'
    with engine.begin() as c:
        versions=c.execute(sa.text("SELECT revision,snapshot->>'value_prop' FROM charter_versions WHERE charter_id=:cid ORDER BY revision"),{'cid':cid}).all()
        assert versions==[(2,'Initial'),(3,'Updated')]
        c.execute(db.charters.update().where(db.charters.c.id==cid).values(is_frozen=True,revision=4))
    with pytest.raises(sa.exc.DBAPIError,match='append-only'):
        with engine.begin() as c: c.execute(sa.text('DELETE FROM charter_versions WHERE charter_id=:cid'),{'cid':cid})
    refused=api.put(path,headers=headers(4),json={'fieldsCharter':{'valueProp':'Changed'}})
    assert refused.status_code==422 and refused.json()['code']=='BASELINE_FROZEN'
    with engine.connect() as c:
        assert c.scalar(sa.select(db.charters.c.value_prop).where(db.charters.c.id==cid))=='Updated'
