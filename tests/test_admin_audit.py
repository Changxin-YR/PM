from secrets import token_urlsafe
from uuid import uuid4
import httpx
import sqlalchemy as sa
from argon2 import PasswordHasher
from pmo import schema_v1 as db


def test_audit_scope_no_admin_role_inheritance(live,actor):
    engine, api, port, _=live
    local, cred=actor(extra_actions=('admin_audits_list',))
    other,_=actor()
    def create(secret):
        response=api.post('/api/v1/projects',headers={'Authorization':'Bearer '+secret,'Idempotency-Key':uuid4().hex},json={'code':'AUD','name':'Scoped'})
        assert response.is_success
        return response.headers['X-Request-ID']
    ours,theirs=create(local),create(other)
    password=token_urlsafe(24)
    with engine.begin() as c:
        user=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==cred))
        username=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==user).values(password_hash=PasswordHasher().hash(password)).returning(db.user_accounts.c.username))
        c.execute(db.persons.update().where(db.persons.c.user_id==user).values(role='system_admin'))
    path='/api/v1/admin/audits'
    with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as human:
        assert human.post('/api/v1/auth/login',json={'username':username,'password':password}).is_success
        denied=human.get(path)
        assert denied.status_code==403
        with engine.begin() as c: c.execute(db.user_roles.insert().values(user_id=user,role='system_admin'))
        assert api.get(path,headers={'Authorization':'Bearer '+local}).status_code==403
        for rid,action,result in [(ours,'pmo_project_create','success'),(denied.headers['X-Request-ID'],'admin_audits_list','denied')]:
            rows=human.get(path,params={'requestId':rid,'result':result}).json()['data']['items']
            assert len(rows)==1 and rows[0]['action']==action
        assert human.get(path,params={'requestId':theirs}).json()['data']['total']==0
        assert human.get(path,params={'page':2,'pageSize':1}).json()['data']['page']==2
        with engine.begin() as c: c.execute(db.user_roles.delete().where(db.user_roles.c.user_id==user))
        assert human.get(path).status_code==403
