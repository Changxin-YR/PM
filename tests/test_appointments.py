from contextlib import ExitStack
from secrets import token_urlsafe
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from argon2 import PasswordHasher

from pmo import schema_v1 as db


@pytest.mark.parametrize('decision', ['accept','decline'])
def test_separate_nominate_issue_and_decide(live,actor,decision):
    engine,api,port,_=live
    ops=('pmo_role_assignment_propose','human_accept_role_assignment','human_issue_role_invitation')
    token,cred=actor(extra_actions=ops)
    headers={'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex}
    project=api.post('/api/v1/projects',headers=headers,json={'code':'HIRE','name':'Appointments'}).json()['data']
    pid=project['projectId']; path='projects/'+pid
    org=project['orgId']; foreign,_=actor(); other=api.get('/api/v1/projects',headers={'Authorization':'Bearer '+foreign})
    assert other.json()['data']['total']==0
    password=token_urlsafe(24); digest=PasswordHasher().hash(password)
    with ExitStack() as stack:
        def login():
            person=uuid4(); user=uuid4().hex
            with engine.begin() as c:
                uid=c.scalar(db.user_accounts.insert().values(username=user,password_hash=digest,is_active=True).returning(db.user_accounts.c.id))
                c.execute(db.persons.insert().values(id=person,org_id=org,user_id=uid,created_by=person,name='Synthetic',is_active=True))
            client=stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/',trust_env=False))
            client.headers['X-CSRF-Token']=client.post('auth/login',json={'username':user,'password':password}).json()['csrfToken']
            return person,client
        issuer,office=login(); nominee,recipient=login(); _,stranger=login()
        def key(rev): return {'Idempotency-Key':uuid4().hex,'If-Match':str(rev)}
        proposal={'personId':str(nominee),'role':'pm'}; keys={**headers,**key(1)}
        create='/api/v1/'+path+'/role-invitations'
        with engine.begin() as c:
            foreign_person=c.scalar(sa.select(db.persons.c.id).where(db.persons.c.org_id!=org))
        assert api.post(create,headers=keys,json={**proposal,'personId':str(foreign_person)}).status_code==404
        assert api.post(create,headers={**keys,'If-Match':'2'},json=proposal).status_code==409
        proposed=api.post(create,headers=keys,json=proposal).json()['data']
        invite='role-invitations/'+proposed['invitationId']+'/'
        assert api.post(create,headers=keys,json=proposal).json()['data']==proposed
        assert recipient.get('me/role-invitations').json()['data']==[] and recipient.get(path).status_code==404
        assert office.post(invite+'issue',headers=key(1)).status_code==403
        assert recipient.post(invite+'accept',headers=key(1)).status_code==409
        for action in ['issue','accept']:
            assert api.post('/api/v1/'+invite+action,headers=keys).status_code==403
        with engine.begin() as c:
            for person in (issuer,nominee):
                c.execute(sa.text("INSERT INTO appointment_authorities(org_id,person_id,role,authority_ref,expires_at) VALUES(:o,:p,'pm','SYNTHETIC',now()+interval '1 hour')"),dict(o=org,p=person))
        assert recipient.post(invite+'issue',headers=key(1)).status_code==403
        assert len(office.get('me/appointment-queue').json()['data'])==1
        issuekey=key(1); issued=office.post(invite+'issue',headers=issuekey)
        assert issued.status_code==200 and issued.json()==office.post(invite+'issue',headers=issuekey).json()
        items=recipient.get('me/role-invitations').json()['data']; assert len(items)==1 and items[0]['revision']==2
        assert stranger.get('me/role-invitations').json()['data']==[] and recipient.get(path).status_code==404
        assert stranger.post(invite+decision,headers=key(2)).status_code==404
        assert recipient.post(invite+decision,headers=key(1)).status_code==409
        if decision=='accept':
            with engine.begin() as c: c.execute(sa.text('UPDATE appointment_authorities SET revoked_at=now() WHERE person_id=:p'),dict(p=issuer))
            assert recipient.post(invite+decision,headers=key(2)).status_code==403
            with engine.begin() as c: c.execute(sa.text('UPDATE appointment_authorities SET revoked_at=NULL WHERE person_id=:p'),dict(p=issuer))
        finalkey=key(2); response=recipient.post(invite+decision,headers=finalkey)
        assert response.status_code==200,response.text
        assert recipient.post(invite+decision,headers=finalkey).json()==response.json()
        assert recipient.get('me/role-invitations').json()['data']==[]
        assert recipient.get(path).status_code==(200 if decision=='accept' else 404)
        with engine.connect() as c:
            assert c.scalar(sa.select(db.projects.c.pm_id).where(db.projects.c.id==pid))==(nominee if decision=='accept' else None)
            assert c.scalar(sa.select(sa.func.count()).select_from(db.project_members).where(db.project_members.c.project_id==pid))==(1 if decision=='accept' else 0)
