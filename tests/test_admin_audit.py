from secrets import token_urlsafe
from uuid import uuid4
import httpx
import sqlalchemy as sa
from argon2 import PasswordHasher
from pmo import schema_v1 as db
from pmo.audit_store import audit
from pmo.projects import fingerprint


def test_audit_scope_no_admin_role_inheritance(live,actor):
    engine, api, port, _=live
    local, cred=actor(extra_actions=('admin_audits_list',))
    other,_=actor()
    def create(secret):
        response=api.post('/api/v1/projects',headers={'Authorization':'Bearer '+secret,'Idempotency-Key':uuid4().hex},
            json={'code':'AUD','name':'Scoped','description':'Original synthetic description'})
        assert response.is_success
        return response.headers['X-Request-ID'], response.json()['data']['projectId']
    (ours, pid), (theirs, other_pid) = create(local), create(other)
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
            if result == 'success':
                assert rows[0]['sourceRefs'] == [f'project:{pid}@1'] and len(rows[0]['responseHash']) == 64
        assert human.get(path,params={'requestId':theirs}).json()['data']['total']==0
        target = f'/api/v1/projects/{pid}'
        agent = {'Authorization': 'Bearer ' + local}
        read = api.get(target, headers=agent).raise_for_status()
        rid = read.headers['X-Request-ID']
        traced, = human.get(path, params={'requestId': rid}).raise_for_status().json()['data']['items']
        assert (traced['actorType'], traced['actorId'], traced['delegatedBy'], traced['projectId']) == (
            'agent', str(cred), str(user), pid)
        assert traced['action'] == 'pmo_project_get' and traced['result'] == 'success'
        assert traced['sourceRefs'] == read.json()['meta']['sourceRefs'] == [f'project:{pid}@1']
        assert traced['ruleVersion'] == read.json()['meta']['ruleVersion']
        assert traced['responseHash'] == fingerprint(read.json())
        assert 'previousHash' not in traced
        with engine.connect() as c:
            assert c.scalar(sa.select(audit.c.request_path).where(audit.c.request_id == rid)) == '/api/v1/projects/{projectId}'
            assert c.scalar(sa.select(db.outbox_events.c.id).where(db.outbox_events.c.dedupe_key == rid)) is None
        blocked = api.get(f'/api/v1/projects/{other_pid}', headers=agent)
        assert blocked.status_code == 404
        failure, = human.get(path, params={'requestId': blocked.headers['X-Request-ID']}).json()['data']['items']
        assert failure['result'] == 'denied' and 'sourceRefs' not in failure and 'projectId' not in failure
        assert 'previousHash' not in failure and 'responseHash' not in failure

        with engine.begin() as c:
            c.exec_driver_sql('CREATE TRIGGER prevent_agent_read_audit BEFORE INSERT ON audit_logs '
                              'FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
        try:
            failed = api.get(target, headers=agent)
            assert (failed.status_code, failed.json()['code']) == (500, 'DATABASE_FAILURE')
            assert 'data' not in failed.json()
            assert human.get(path, params={'requestId': failed.headers['X-Request-ID']}).json()['data']['total'] == 0
        finally:
            with engine.begin() as c:
                c.exec_driver_sql('DROP TRIGGER prevent_agent_read_audit ON audit_logs')
        assert human.get(path,params={'page':2,'pageSize':1}).json()['data']['page']==2
        key = uuid4().hex
        change = api.patch(target, headers={**agent, 'Idempotency-Key': key, 'If-Match': '1'},
            json={'fields': {'name': 'Audit receipt for an authorized edit'}}).raise_for_status()
        updated, = human.get(path, params={'requestId': change.headers['X-Request-ID']}).raise_for_status().json()['data']['items']
        assert updated['action'] == 'pmo_project_draft_update'
        assert updated['previousHash'] == fingerprint({'name': 'Scoped', 'description': 'Original synthetic description', 'revision': 1})
        assert updated['responseHash'] == fingerprint(change.json())
        assert updated['sourceRefs'] == [f'project:{pid}@2']
        assert 'idempotencyKey' not in updated and key not in str(updated)
        with engine.begin() as c: c.execute(db.user_roles.delete().where(db.user_roles.c.user_id==user))
        assert human.get(path).status_code==403
