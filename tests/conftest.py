"""A fresh PostgreSQL schema per run; never truncate a pre-existing table."""
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from pmo import schema_v1 as db
from pmo.session_schema import attempts

@pytest.fixture(scope='session')
def live_stack(tmp_path_factory):
    admin=sa.create_engine(os.environ['PMO_TEST_DATABASE_URL'],hide_parameters=True)
    schema='pmo_test_'+uuid4().hex
    with admin.begin() as c: c.exec_driver_sql(f'CREATE SCHEMA {schema}')
    url=admin.url.update_query_dict({'options':f'-csearch_path={schema}'}).render_as_string(hide_password=False)
    engine=sa.create_engine(url,hide_parameters=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    env={**os.environ,'DATABASE_URL':url,'PYTHONUTF8':'1','PMO_LOCAL_HTTP':'1','PMO_EVIDENCE_DIR':str(tmp_path_factory.mktemp('private_evidence')),
         'PMO_REPORT_ANALYSIS':'0'}
    proc=None
    env['PMO_SYNTHETIC_POLICY_SCHEMA']=schema
    try:
        for args in [('upgrade','head'),('downgrade','base'),('upgrade','head')]:
            subprocess.run([sys.executable,'-m','alembic',*args],env=env,check=True)
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','pmo.api:app','--port',str(port),'--no-access-log'],env=env)
        base=f'http://127.0.0.1:{port}'
        with httpx.Client(base_url=base,trust_env=False) as api:
            for _ in range(120):
                try:
                    if api.get('/openapi.json').status_code==200: break
                except httpx.ConnectError: pass
                if proc.poll() is not None: pytest.fail('API exited during startup')
                time.sleep(.15)
            else: pytest.fail('API startup timed out')
            yield engine,api,port,env
    finally:
        if proc is not None:
            proc.terminate(); proc.wait(timeout=10)
        engine.dispose()
        with admin.begin() as c: c.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        admin.dispose()


@pytest.fixture
def live(live_stack):
    # All HTTP fixtures use one loopback IP. Expire only its test window across cases;
    # per-account buckets and throttling within each case still use the real handler.
    engine, _, _, env = live_stack
    with engine.begin() as c:
        assert c.scalar(sa.text('SELECT current_schema()')) == env['PMO_SYNTHETIC_POLICY_SCHEMA']
        loopback = hashlib.sha256(b'ip:127.0.0.1').hexdigest()
        c.execute(attempts.update().where(attempts.c.bucket == loopback).values(
            started_at=sa.func.now() - sa.text("interval '11 minutes'")))
    return live_stack


@pytest.fixture
def actor(live):
    def make(grant=True, extra_actions=()):
        token=secrets.token_urlsafe(32)
        with live[0].begin() as c:
            org=c.scalar(db.organizations.insert().values(name='Synthetic',status='active').returning(db.organizations.c.id))
            user=c.scalar(db.user_accounts.insert().values(username=uuid4().hex,password_hash='!disabled!',is_active=True).returning(db.user_accounts.c.id))
            person=uuid4(); c.execute(db.persons.insert().values(id=person,created_by=person,user_id=user,org_id=org,name='Fixture',is_active=True))
            digest=hashlib.sha256(token.encode()).hexdigest()
            actions=['pmo_project_list','pmo_project_create','pmo_project_get','pmo_project_draft_update','pmo_milestone_create','pmo_milestone_list','pmo_milestone_draft_update',*extra_actions]
            credential=c.scalar(db.agent_credentials.insert().values(delegated_by=user,org_id=org,allow_project_create=grant,allowed_project_ids=[],allowed_actions=actions,expires_at=datetime.now(UTC)+timedelta(minutes=10),status='active',token_hash=digest,token_fingerprint=digest[:12]).returning(db.agent_credentials.c.id))
        return token,credential
    return make
