from datetime import date
from uuid import uuid4

import sqlalchemy as sa

from pmo import outbox, schema_v1 as db
from pmo.identity import explicit_grants


def test_forecast_alert_episode_delivery_and_recovery(live,actor):
    engine,api,_,_=live
    ops=('pmo_milestone_forecast','pmo_alert_list','human_alerts_overview')
    secret,cid=actor(extra_actions=ops); other,_=actor(extra_actions=ops)
    def headers(n=1,token=secret): return {'Authorization':'Bearer '+token,'If-Match':str(n),'Idempotency-Key':uuid4().hex}
    p=api.post('/api/v1/projects',headers=headers(),json={'name':'Frozen plan','code':'DELAY'}).json()['data']; pid=p['projectId']
    base=f'/api/v1/projects/{pid}'; alert_path=base+'/alerts'
    def milestone(key): return api.post(base+'/milestones',headers=headers(),json={'name':'Delivery','plannedDate':'2027-01-15','isKey':key}).json()['data']['milestoneId']
    mid=milestone(True); ordinary=milestone(False)
    def change(day,rev=1,mid=mid,h=None): return api.patch(f'/api/v1/milestones/{mid}/forecast',headers=h or headers(rev),json={'newDate':f'2027-01-{day:02}','reason':'Supplier changed slot'})
    with engine.begin() as c:
        pm=c.scalar(sa.select(db.projects.c.initiator_id).where(db.projects.c.id==pid))
        account=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==cid))
        c.execute(db.project_members.insert().values(project_id=pid,person_id=pm,assigned_by=pm,role='pm',assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(explicit_grants.insert().values(credential_id=cid,project_id=pid,granted_by=account))
        c.execute(db.projects.update().where(db.projects.c.id==pid).values(stage='executing',pm_id=pm))
    assert api.get(alert_path,headers=headers(token=other)).status_code==404 and change(17,h=headers(token=other)).status_code==404
    assert api.get('/api/v1/analytics/health-alerts',headers=headers()).status_code==403
    assert change(20,mid=ordinary).status_code==200 and api.get(alert_path,headers=headers()).json()['data']['total']==0
    key=headers(); raised=change(16,h=key)
    assert raised.status_code==200 and raised.json()==change(16,h=key).json() and raised.json()['data']['plannedDate']=='2027-01-15'
    assert change(17,1).status_code==409 and change(16,2).status_code==422
    assert change(17,2).status_code==200
    first=api.get(alert_path,headers=headers()).json()['data']; assert first['total']==1
    event=first['items'][0]; assert event['severity']=='yellow' and event['status']=='open' and event['sourceRefs']==[f'milestone:{mid}@2']
    # Process only this rule event, including a committed delivery with no acknowledgement yet.
    with engine.begin() as c:
        eid=c.scalar(sa.select(db.outbox_events.c.id).where(db.outbox_events.c.event_type=='rule.notify',db.outbox_events.c.payload['eventId'].astext==event['alertId']))
        c.execute(db.outbox_events.update().where(db.outbox_events.c.id==eid).values(next_attempt_at=sa.text("now()-interval '1 day'")))
        assert c.scalar(sa.select(sa.func.count()).select_from(db.notifications).where(db.notifications.c.project_id==pid))==0
    lease=outbox.claim(engine); assert lease.event_id==eid
    assert outbox.consume(engine,lease) and outbox.consume(engine,lease) and outbox.acknowledge(engine,lease)
    with engine.connect() as c:
        delivered=c.execute(sa.select(db.notifications).where(db.notifications.c.project_id==pid)).mappings().one()
        assert delivered.recipient_person_id==pm and str(delivered.rule_event_id)==event['alertId'] and delivered.delivery_status=='delivered'
        assert c.execute(sa.text('SELECT planned_date,prior_date,new_date,reason FROM milestone_forecasts WHERE milestone_id=:mid ORDER BY revision'),{'mid':mid}).all()==[
            (date(2027,1,15),None,date(2027,1,16),'Supplier changed slot'),(date(2027,1,15),date(2027,1,16),date(2027,1,17),'Supplier changed slot')]
    assert change(15,3).status_code==200 and api.get(alert_path,headers=headers()).json()['data']['items'][0]['status']=='resolved'
    assert change(18,4).status_code==200
    alerts=api.get(alert_path,headers=headers()).json()['data']['items']; assert [i['status'] for i in alerts]==['open','resolved']
    with engine.begin() as c:
        c.execute(db.milestones.update().where(db.milestones.c.id==mid).values(actual_date=date(2027,1,17)))
    assert change(19,5).json()['code']=='MILESTONE_LOCKED'
