"""Run explicitly after npm ci: pytest tests/browser_case.py."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import httpx
import sqlalchemy as sa
from argon2 import PasswordHasher
from pmo import schema_v1 as db

def test_browser_draft_workflow(live,actor):
    engine,_,api_port,_=live
    _,aid=actor(); password=secrets.token_urlsafe(32)
    with engine.begin() as c:
        uid=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==aid))
        username=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==uid).values(password_hash=PasswordHasher().hash(password)).returning(db.user_accounts.c.username))
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
        subprocess.run(['node','e2e/draft.mjs'],cwd=root,check=True,timeout=120,env={**os.environ,'PMO_BROWSER_URL':url,'PMO_TEST_USER':username,'PMO_TEST_PASSWORD':password})
    finally: proc.terminate(); proc.wait(timeout=10)
