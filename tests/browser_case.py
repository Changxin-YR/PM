"""Run explicitly after npm ci: pytest tests/browser_case.py."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
from base64 import b64encode
from io import BytesIO
from PIL import Image
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
        for role in ['issuer','nominee','baseline_pm','baseline_client']:
            name=uuid4().hex; person=uuid4(); secret=secrets.token_urlsafe(32)
            user=c.scalar(db.user_accounts.insert().values(username=name,password_hash=PasswordHasher().hash(secret),is_active=True).returning(db.user_accounts.c.id))
            c.execute(db.persons.insert().values(id=person,created_by=person,org_id=org,user_id=user,name=role,is_active=True))
            accounts.update({f'PMO_TEST_{role.upper()}':name,f'PMO_TEST_{role.upper()}_PASSWORD':secret,f'PMO_TEST_{role.upper()}_ID':str(person)})
            if role=='issuer':
                c.execute(sa.text("INSERT INTO appointment_authorities VALUES(:org,:person,'pm','SYNTHETIC E2E',now()+interval '1 hour',NULL)"),dict(person=person,org=org))
            if role=='nominee': c.execute(db.user_roles.insert().values(user_id=user,role='finance_verifier'))
        pm=accounts['PMO_TEST_BASELINE_PM_ID']; client=accounts['PMO_TEST_BASELINE_CLIENT_ID']
        pid=c.scalar(db.projects.insert().values(code='E2E-BASE',name='基线签署演练',org_id=org,stage='designing',pm_id=pm,created_by=pm,initiator_id=pm,revision=1).returning(db.projects.c.id))
        for person,role in ((pm,'pm'),(client,'client_representative')):
            c.execute(db.project_members.insert().values(project_id=pid,person_id=person,assigned_by=pm,role=role,assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(db.stakeholders.insert().values(project_id=pid,created_by=pm,name='测试客户代表',type='customer',linked_person_id=client))
        c.execute(db.requirements.insert().values(project_id=pid,created_by=pm,desc='导出归档清单',priority='P0',acceptance_criteria='导出保留全部来源编号',status='baseline',baseline_version=0))
        evidence_pid=c.scalar(db.projects.insert().values(code='E2E-FILE',name='完工凭据演练',org_id=org,stage='designing',pm_id=pm,created_by=pm,initiator_id=pm,revision=1).returning(db.projects.c.id))
        for person,role in ((pm,'pm'),(client,'reviewer'),(client,'client_representative')):
            c.execute(db.project_members.insert().values(project_id=evidence_pid,person_id=person,assigned_by=pm,role=role,assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(db.milestones.insert().values(project_id=evidence_pid,created_by=pm,name='试点验收',ordinal=1,is_key=True,planned_date='2020-03-09'))
        c.execute(db.stakeholders.insert().values(project_id=evidence_pid,created_by=pm,name='测试客户代表',type='customer',linked_person_id=client))
        req=c.scalar(db.requirements.insert().values(project_id=evidence_pid,created_by=pm,desc='完成文件交付',acceptance_criteria='文件中列明验收记录',priority='P0',status='baseline',baseline_version=0).returning(db.requirements.c.id))
        accounts['PMO_TEST_REQUIREMENT_ID']=str(req)
        c.execute(db.change_entries.insert().values(project_id=evidence_pid,created_by=pm,requested_by=pm,target_type='requirement',target_id=req,base_revision=1,
            status='withdrawn',before={'priority':'P0'},after={'priority':'P1'},reason='验收样例：调整优先级已撤回'))
        png=BytesIO(); Image.new('RGB',(6,6),(120,90,66)).save(png,'PNG'); accounts['PMO_TEST_PNG']=b64encode(png.getvalue()).decode('ascii')
    for role,sign_as in [('BASELINE_PM','pm'),('BASELINE_CLIENT','client_representative')]:
        with httpx.Client(base_url=f'http://127.0.0.1:{api_port}/api/v1/',trust_env=False) as signer:
            signer.headers['X-CSRF-Token']=signer.post('auth/login',json={'username':accounts['PMO_TEST_'+role],'password':accounts['PMO_TEST_'+role+'_PASSWORD']}).json()['csrfToken']
            path=f'projects/{evidence_pid}/requirement-baseline'; state=signer.get(path).json()['data']
            signer.post(path+'/sign',headers={'If-Match':str(state['revision']),'Idempotency-Key':uuid4().hex},json={'contentHash':state['contentHash'],'role':sign_as,'reason':'Browser fixture baseline'}).raise_for_status()
    with engine.begin() as c: c.execute(db.projects.update().where(db.projects.c.id==evidence_pid).values(stage='executing'))
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
