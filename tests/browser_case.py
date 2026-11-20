"""Run explicitly after npm ci: pytest tests/browser_case.py."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from uuid import uuid4
import httpx
import sqlalchemy as sa
from argon2 import PasswordHasher
from pmo import schema_v1 as db

def test_browser_draft_workflow(live,actor):
    engine,_,api_port,_=live
    _,aid=actor(); password=secrets.token_urlsafe(32)
    with engine.begin() as c:
        uid=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==aid))
        c.execute(db.user_roles.insert().values(user_id=uid,role='system_admin'))
        c.execute(db.user_roles.insert().values(user_id=uid,role='delegator'))
        username=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==uid).values(password_hash=PasswordHasher().hash(password)).returning(db.user_accounts.c.username))
        org=c.scalar(sa.select(db.persons.c.org_id).where(db.persons.c.user_id==uid))
        accounts={}
        for role in ['issuer','nominee']:
            name=uuid4().hex; person=uuid4(); secret=secrets.token_urlsafe(32)
            user=c.scalar(db.user_accounts.insert().values(username=name,password_hash=PasswordHasher().hash(secret),is_active=True).returning(db.user_accounts.c.id))
            c.execute(db.persons.insert().values(id=person,created_by=person,org_id=org,user_id=user,name=role,is_active=True))
            accounts.update({f'PMO_TEST_{role.upper()}':name,f'PMO_TEST_{role.upper()}_PASSWORD':secret,f'PMO_TEST_{role.upper()}_ID':str(person)})
            if role=='issuer':
                c.execute(sa.text("INSERT INTO appointment_authorities VALUES(:org,:person,'pm','SYNTHETIC E2E',now()+interval '1 hour',NULL)"),dict(person=person,org=org))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    root=Path(__file__).resolve().parents[1]/'web'
    url=f'http://127.0.0.1:{port}'
    proc=subprocess.Popen(['node','node_modules/vite/bin/vite.js','--port',str(port)],cwd=root,env={**os.environ,'PMO_API_PORT':str(api_port)})
    try:
        with httpx.Client(trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get(url).status_code==200: break
                except httpx.ConnectError: pass
                time.sleep(.15)
            else: raise RuntimeError('Vite startup failed')
        subprocess.run(['node','e2e/draft.mjs'],cwd=root,check=True,timeout=120,env={**os.environ,**accounts,'PMO_BROWSER_URL':url,'PMO_TEST_USER':username,'PMO_TEST_PASSWORD':password})
    finally: proc.terminate(); proc.wait(timeout=10)
