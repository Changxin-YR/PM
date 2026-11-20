import asyncio
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
import os
import sys
from secrets import token_urlsafe
from uuid import uuid4

from argon2 import PasswordHasher
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sqlalchemy as sa

from pmo import schema_v1 as db


def login(stack,live,credential):
    engine,_,port,_=live
    secret=token_urlsafe(24)
    with engine.begin() as c:
        uid=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==credential))
        name=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==uid).values(password_hash=PasswordHasher().hash(secret)).returning(db.user_accounts.c.username))
    client=stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/',trust_env=False))
    client.headers['X-CSRF-Token']=client.post('auth/login',json={'username':name,'password':secret}).json()['csrfToken']
    return client,uid


def headers(rev): return {'If-Match':str(rev),'Idempotency-Key':uuid4().hex}


def test_issue_once_use_stdio_then_revoke_without_restart(live,actor):
    engine,api,port,_=live
    legacy,cred=actor(extra_actions=('human_agent_token_create','admin_agent_token_revoke'))
    path='me/agent-tokens'
    with ExitStack() as stack:
        human,uid=login(stack,live,cred)
        pid=human.post('projects',headers=headers(1),json={'code':'GRANT','name':'Delegated'}).json()['data']['projectId']
        body={'allowedProjectIds':[pid],'allowedActions':['pmo_project_get'],'expiresAt':(datetime.now(UTC)+timedelta(hours=1)).isoformat()}
        collection=human.get(path).json()['data']; h=headers(collection['collectionRevision'])
        assert human.post(path,json=body,headers=h).status_code==403
        with engine.begin() as c: c.execute(db.user_roles.insert().values(user_id=uid,role='delegator'))
        assert api.post('/api/v1/'+path,headers={**h,'Authorization':'Bearer '+legacy},json=body).status_code==403
        for change,status in [({'allowedActions':['human_agent_token_revoke']},403),({'allowProjectCreate':True},403),
                              ({'allowedProjectIds':[str(uuid4())]},404),({'expiresAt':(datetime.now(UTC)+timedelta(days=2)).isoformat()},422)]:
            assert human.post(path,json={**body,**change},headers=h).status_code==status
        first=human.post(path,json=body,headers=h)
        assert first.status_code==200 and first.headers['cache-control']=='no-store'
        secret=first.headers['X-PMO-New-Token']; receipt=first.json()['data']; tid=receipt['tokenId']
        repeat=human.post(path,json=body,headers=h)
        assert repeat.json()==first.json() and 'X-PMO-New-Token' not in repeat.headers
        assert human.post(path,json=body,headers=headers(collection['collectionRevision'])).status_code==409
        assert secret not in first.text and secret not in human.get(path).text
        with engine.connect() as c:
            idem=c.execute(sa.select(db.idempotency_keys).where(db.idempotency_keys.c.resource_id==tid)).mappings().one()
            assert secret not in str(idem) and idem.expires_at-idem.created_at>=timedelta(days=89)
            assert c.scalar(sa.select(db.agent_credentials.c.token_hash).where(db.agent_credentials.c.id==tid))!=secret
        async def exercise():
            env={**{k:os.environ[k] for k in ('SYSTEMROOT','PATH','TEMP','TMP') if k in os.environ},'PYTHONUTF8':'1','PMO_API_PORT':str(port),'PMO_AGENT_TOKEN':secret}
            params=StdioServerParameters(command=sys.executable,args=['-m','pmo.mcp_server'],env=env)
            async with stdio_client(params) as streams, ClientSession(*streams) as session:
                await session.initialize()
                args={'projectId':pid}
                ok=await session.call_tool('pmo_project_get',args)
                assert not ok.isError and ok.structuredContent['data']['project']['projectId']==pid
                assert (await session.call_tool('pmo_project_list',{})).isError
                rh=headers(receipt['revision']); reason={'reason':'Integration complete'}; url=path+'/'+tid+'/revoke'
                revoked=human.post(url,headers=rh,json=reason)
                assert revoked.json()['data']['state']=='revoked' and human.post(url,headers=rh,json=reason).json()==revoked.json()
                denied=await session.call_tool('pmo_project_get',args)
                assert denied.isError and denied.structuredContent['error']['code']=='UNAUTHORIZED'
        asyncio.run(exercise())


def test_admin_scope_and_live_issuer_authority(live,actor):
    engine,api,_,_=live
    _,cred=actor(); _,foreign=actor()
    with ExitStack() as stack:
        owner,uid=login(stack,live,cred); outsider,other=login(stack,live,foreign)
        with engine.begin() as c:
            c.execute(db.user_roles.insert(),[dict(user_id=uid,role='delegator'),dict(user_id=other,role='system_admin')])
        path='me/agent-tokens'; admin='admin/agents/tokens'; version=owner.get(path).json()['data']['collectionRevision']
        body={'allowedProjectIds':[],'allowedActions':['pmo_project_create','pmo_project_get'],'allowProjectCreate':True,'expiresAt':(datetime.now(UTC)+timedelta(minutes=20)).isoformat()}
        first=owner.post(path,headers=headers(version),json=body); token=first.json()['data']; tid=token['tokenId']; secret=first.headers['X-PMO-New-Token']
        auth={'Authorization':'Bearer '+secret,'Idempotency-Key':uuid4().hex}
        pid=api.post('/api/v1/projects',headers=auth,json={'code':'AFTER','name':'After issuance'}).json()['data']['projectId']
        assert owner.get(admin).status_code==403 and tid not in outsider.get(admin).text
        assert outsider.post(f'{admin}/{tid}/revoke',headers=headers(2),json={'reason':'Outside scope'}).status_code==404
        assert owner.post(f'{path}/{tid}/revoke',headers=headers(1),json={'reason':'Old scope'}).status_code==409
        with engine.begin() as c:
            c.execute(db.user_roles.delete().where(db.user_roles.c.user_id==uid,db.user_roles.c.role=='delegator'))
            c.execute(db.user_roles.insert().values(user_id=uid,role='system_admin'))
        assert api.get('/api/v1/projects/'+pid,headers=auth).status_code==401
        assert owner.post(f'{admin}/{tid}/revoke',headers=headers(2),json={'reason':'Remove ability'}).json()['data']['state']=='revoked'
