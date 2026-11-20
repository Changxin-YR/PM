from contextlib import ExitStack
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4
from zipfile import ZipFile

import httpx
from argon2 import PasswordHasher
from PIL import Image
from pypdf import PdfWriter
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.evidence_storage import LIMIT, MIMES, validate


def binary(kind):
    buf=BytesIO()
    if kind in ('png','jpg'): Image.new('RGB',(4,4),(12,90,70)).save(buf,'PNG' if kind=='png' else 'JPEG')
    elif kind=='pdf':
        writer=PdfWriter(); writer.add_blank_page(200,200); writer.write(buf)
    else:
        with ZipFile(buf,'w') as z:
            z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/></w:body></w:document>')
    return buf.getvalue()


def test_allowed_evidence_formats():
    for kind in ('png','jpg','docx','pdf'): validate('record.'+kind,MIMES['.'+kind],binary(kind))


def test_private_evidence_to_milestone(live,actor):
    engine,api,port,env=live
    token,cred=actor(extra_actions=('human_evidence_upload','human_evidence_get','human_evidence_verify','human_milestone_complete'))
    def h(n=1): return {'Idempotency-Key':uuid4().hex,'If-Match':str(n)}
    bearer={'Authorization':'Bearer '+token}
    p=api.post('/api/v1/projects',headers={**h(),**bearer},json={'code':'PROOF','name':'Proof chain'}).json()['data']; pid=p['projectId']
    m=lambda name:api.post(f'/api/v1/projects/{pid}/milestones',headers={**h(),**bearer},json={'name':name,'plannedDate':'2020-03-01','isKey':True}).json()['data']['milestoneId']
    mid=m('Delivery'); second=m('Other'); path=f'projects/{pid}/evidence'
    digest=PasswordHasher(); pwd=token_urlsafe(); hashed=digest.hash(pwd)
    with engine.begin() as c:
        uid=c.scalar(sa.select(db.agent_credentials.c.delegated_by).where(db.agent_credentials.c.id==cred)); pm=c.scalar(sa.select(db.persons.c.id).where(db.persons.c.user_id==uid))
        owner=c.scalar(db.user_accounts.update().where(db.user_accounts.c.id==uid).values(password_hash=hashed).returning(db.user_accounts.c.username))
        def new_user():
            name=uuid4().hex; person=uuid4(); u=c.scalar(db.user_accounts.insert().values(username=name,password_hash=hashed,is_active=True).returning(db.user_accounts.c.id))
            c.execute(db.persons.insert().values(id=person,created_by=pm,org_id=p['orgId'],user_id=u,name='Fixture',is_active=True)); return name,person
        reviewer,reviewer_id=new_user(); outsider,_=new_user()
        for person,role in ((pm,'pm'),(pm,'reviewer'),(reviewer_id,'reviewer')):
            c.execute(db.project_members.insert().values(project_id=pid,person_id=person,assigned_by=pm,role=role,assignment_status='accepted',accepted_at=sa.func.now()))
        c.execute(db.projects.update().where(db.projects.c.id==pid).values(stage='executing',pm_id=pm))
    with ExitStack() as stack:
        def login(name):
            cli=stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1/',trust_env=False)); cli.headers['X-CSRF-Token']=cli.post('auth/login',json={'username':name,'password':pwd}).json()['csrfToken']; return cli
        manager,check,foreign=map(login,(owner,reviewer,outsider)); data=binary('png'); f={'file':('review.png',data,'image/png')}; form={'linkedResourceType':'milestone','linkedResourceId':mid,'description':'Evidence from inspection'}; k=h()
        assert api.post('/api/v1/'+path,headers={**bearer,**k},files=f,data=form).status_code==403
        assert foreign.post(path,headers=k,files=f,data=form).status_code==404
        assert manager.post(path,headers=k,files=f,data={**form,'sha256':'0'*64}).status_code==422
        assert manager.post(path,headers=k,files=f,data={**form,'linkedResourceId':str(uuid4())}).status_code==404
        assert manager.post(path,headers=k,files={'file':('scan.pdf',data,'application/pdf')},data=form).status_code==422
        assert manager.post(path,headers=k,files={'file':('big.png',b'x'*(LIMIT+1),'image/png')},data=form).status_code==413
        result=manager.post(path,headers=k,files=f,data=form); assert result.status_code==200,result.text
        assert manager.post(path,headers=k,files=f,data=form).json()==result.json()
        row=result.json()['data']; eid=row['evidenceId']; ep='evidence/'+eid; comp='milestones/'+mid+'/complete'; cmd={'evidenceRef':row['evidenceRef'],'actualDate':'2020-03-01'}
        assert row['sha256']==sha256(data).hexdigest() and row['status']=='pending_review' and 'storage_key' not in str(row)
        assert manager.get(path).json()['data']['collectionRevision']==2 and manager.post(path,headers=h(),files=f,data=form).status_code==409
        assert foreign.get(ep,params={'download':'true'}).status_code==404 and api.get('/api/v1/'+ep,headers=bearer).status_code==403
        download=check.get(ep,params={'download':'true'}); assert download.content==data and 'attachment' in download.headers['content-disposition']
        decision={'sha256':row['sha256'],'reason':'Compared with source file'}
        assert manager.post(comp,headers=h(),json=cmd).json()['code']=='VERIFIED_EVIDENCE_REQUIRED'
        assert manager.post(ep+'/verify',headers=h(),json=decision).status_code==403
        assert check.post(ep+'/verify',headers=h(),json={**decision,'sha256':'a'*64}).status_code==422
        vkey=h(); verified=check.post(ep+'/verify',headers=vkey,json=decision); assert verified.status_code==200 and verified.json()==check.post(ep+'/verify',headers=vkey,json=decision).json()
        assert manager.post('milestones/'+second+'/complete',headers=h(),json=cmd).json()['code']=='EVIDENCE_TARGET_MISMATCH'
        assert manager.post(comp,headers=h(),json={**cmd,'actualDate':str(date.today()+timedelta(days=10))}).status_code==422
        assert check.post(comp,headers=h(),json=cmd).status_code==403
        with engine.connect() as c: key=c.scalar(sa.select(db.evidence_files.c.storage_key).where(db.evidence_files.c.id==eid))
        file=Path(env['PMO_EVIDENCE_DIR'])/key; file.write_bytes(data[:-1]+bytes([data[-1]^1]))
        assert manager.get(ep,params={'download':'true'}).status_code==409 and manager.post(comp,headers=h(),json=cmd).json()['code']=='EVIDENCE_INTEGRITY_FAILED'
        file.write_bytes(data); ck=h(); closed=manager.post(comp,headers=ck,json=cmd); assert closed.status_code==200,closed.text
        assert closed.json()==manager.post(comp,headers=ck,json=cmd).json() and closed.json()['data']['evidenceRefs']==[row['evidenceRef']]
        assert manager.post(comp,headers=h(2),json=cmd).json()['code']=='MILESTONE_LOCKED'
