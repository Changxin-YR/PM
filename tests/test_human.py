from secrets import token_urlsafe
from uuid import uuid4
import httpx
import sqlalchemy as sa
from argon2 import PasswordHasher
from pmo import schema_v1 as db

def test_human_and_agent_share_project_and_session_revocation(live,actor):
    engine,agent_api,port,_=live
    token,aid=actor(); password=token_urlsafe(32)
    with engine.begin() as c:
        uid=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==aid))
        username=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==uid).values(password_hash=PasswordHasher().hash(password)).returning(db.user_accounts.c.username))
    h={'Authorization':'Bearer '+token,'Idempotency-Key':uuid4().hex}
    project=agent_api.post('/api/v1/projects',headers=h,json={'code':'SHARED','name':'Shared'}).json()['data']
    with httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/',trust_env=False) as human:
        assert human.post('auth/login',json={'username':username,'password':'incorrect'}).status_code==401
        login=human.post('auth/login',json={'username':username,'password':password}); assert login.status_code==200
        assert 'HttpOnly' in login.headers['set-cookie'] and 'samesite=strict' in login.headers['set-cookie'].lower()
        cookie=human.cookies.get('pmo_session'); csrf={'X-CSRF-Token':login.json()['csrfToken']}
        assert human.get('auth/me').json()['accountId']==str(uid)
        assert human.get('projects').json()['data']['items']==[project]
        path='projects/'+project['projectId']; changes={'fields':{'name':'Human edit'}}
        keys={'If-Match':'1','Idempotency-Key':uuid4().hex}
        assert human.patch(path,headers=keys,json=changes).status_code==403
        assert human.patch(path,headers={**keys,**csrf},json=changes).json()['data']['revision']==2
        assert agent_api.get('/api/v1/'+path,headers=h).json()['data']['project']['name']=='Human edit'
        assert human.post('auth/logout',headers=csrf).status_code==204
        assert human.get('auth/me',headers={'Cookie':'pmo_session='+cookie}).status_code==401
    with engine.connect() as c:
        assert c.scalar(sa.select(db.audit_logs.c.actor_type).where(db.audit_logs.c.project_id==project['projectId'],db.audit_logs.c.action=='pmo_project_draft_update'))=='human'
