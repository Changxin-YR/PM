from datetime import date, datetime
from hashlib import sha256
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request, Response
from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.exceptions import HTTPException as FormError

from . import evidence_storage as storage, schema_v1 as db
from .access import Auth, Key, Revision
from .contracts import Result, Strict
from .identity import Failure, visible
from .materials import finish, project_access, replay
from .milestone_forecasts import reconcile
from .milestones import View, view
from .projects import fingerprint, meta, run

router=APIRouter(prefix='/api/v1',tags=['Evidence'])
e=db.evidence_files
links=sa.table('evidence_links',sa.column('evidence_id',sa.Uuid),sa.column('resource_type'),sa.column('resource_id',sa.Uuid),
    sa.column('description'),sa.column('revision',sa.BigInteger),sa.column('verification_note'))
tables={'charter':db.charters,'milestone':db.milestones,'requirement':db.requirements,'review':db.reviews,'risk':db.risks,
    'report':db.reports,'cost_posting':db.cost_postings,'decision':db.decisions}
Kind=Literal['charter','milestone','requirement','review','risk','report','cost_posting','decision']


class Link(Strict):
    linkedResourceType: Kind
    linkedResourceId: UUID
    description: str=Field(default='',max_length=500)


class Evidence(Link):
    evidenceId: UUID
    projectId: UUID
    originalName: str
    mimeType: str
    sizeBytes: int
    sha256: str
    revision: int
    uploadedBy: UUID
    verifiedBy: UUID | None=None
    verifiedAt: datetime | None=None
    status: str
    evidenceRef: str
    canVerify: bool | None=None


class Page(Strict):
    page: int
    pageSize: int
    total: int
    collectionRevision: int
    items: list[Evidence]


class Verify(Strict):
    reason: str=Field(min_length=4,max_length=1000,pattern=r'\S')
    sha256: str=Field(pattern=r'^[0-9a-f]{64}$')


class Complete(Strict):
    actualDate: date
    evidenceRef: str=Field(pattern=r'^evidence:[0-9a-fA-F-]{36}$')
    reason: str | None=Field(default=None,min_length=4,max_length=1000,pattern=r'\S')


def stmt():
    return sa.select(e,links.c.resource_type,links.c.resource_id,links.c.description,links.c.revision).join(links,links.c.evidence_id==e.c.id)


def member(c,a,pid,role):
    m=db.project_members.c
    return bool(c.scalar(sa.select(m.id).where(m.project_id==pid,m.person_id==a.person,m.role==role,m.assignment_status=='accepted',m.accepted_at.is_not(None),m.revoked_at.is_(None)).with_for_update(read=True)))


def item(row,can=None):
    return Evidence(evidenceId=row.id,projectId=row.project_id,originalName=row.original_name,mimeType=row.mime_type,sizeBytes=row.size_bytes,
        sha256=row.sha256,revision=row.revision,uploadedBy=row.uploaded_by,verifiedBy=row.verified_by,verifiedAt=row.verified_at,status=row.quarantine_status,
        evidenceRef=f'evidence:{row.id}',linkedResourceType=row.resource_type,linkedResourceId=row.resource_id,description=row.description,canVerify=can)


def lookup(c,a,eid,lock=False):
    q=stmt().join(db.projects,db.projects.c.id==e.c.project_id).where(e.c.id==eid,visible(a))
    row=c.execute(q.with_for_update(of=e) if lock else q).mappings().one_or_none()
    if row is None: raise Failure(404,'RESOURCE_NOT_FOUND')
    return row


def upload_write(auth,pid,key,expected,link,name,mime,data,digest):
    op='human_evidence_upload'; sig=fingerprint([str(pid),expected,link.model_dump(mode='json'),name,mime,digest])
    def write(c,a,rid):
        p=c.scalar(sa.select(db.projects.c.id).where(db.projects.c.id==pid,visible(a)).with_for_update())
        if not p: raise Failure(404,'RESOURCE_NOT_FOUND')
        t=tables[link.linkedResourceType]
        if not c.scalar(sa.select(t.c.id).where(t.c.id==link.linkedResourceId,t.c.project_id==pid).with_for_update(read=True)): raise Failure(404,'RESOURCE_NOT_FOUND')
        prior=replay(c,a,op,key,sig,Result[Evidence])
        if prior: return prior
        total=c.scalar(sa.select(sa.func.count()).select_from(e).where(e.c.project_id==pid))
        if total+1!=expected: raise Failure(409,'VERSION_CONFLICT')
        storage.validate(name,mime,data)
        storage_key=uuid4().hex
        # Publish only after the private object is durably written. No path or key is returned.
        eid=c.scalar(e.insert().values(project_id=pid,original_name=name,owner_id=a.person,uploaded_by=a.person,sha256=digest,
            size_bytes=len(data),mime_type=mime,storage_key=storage_key,quarantine_status='pending_review').returning(e.c.id))
        c.execute(links.insert().values(evidence_id=eid,resource_type=link.linkedResourceType,resource_id=link.linkedResourceId,description=link.description))
        out=item(lookup(c,a,eid))
        result=finish(c,a,op,key,rid,pid,sig,Result[Evidence](data=out,meta=meta(op,[out.evidenceRef])))
        storage.store(data,storage_key)
        return result
    return run(auth,op,write)


@router.post('/projects/{projectId}/evidence',operation_id='human_evidence_upload',response_model_exclude_none=True,openapi_extra={
    'requestBody':{'required':True,'content':{'multipart/form-data':{'schema':{'type':'object','additionalProperties':False,'properties':{
    'file':{'type':'string','format':'binary'},'linkedResourceType':{'type':'string','enum':list(tables)},'linkedResourceId':{'type':'string','format':'uuid'},
    'description':{'type':'string','maxLength':500}},'required':['file','linkedResourceType','linkedResourceId']}}}}})
async def upload(projectId:UUID,request:Request,authorization:Auth,key:Key,expected:Revision)->Result[Evidence]:
    await run_in_threadpool(run,authorization,'human_evidence_upload',lambda c,a,_:project_access(c,a,projectId))
    if request.headers.get('content-type','').split(';')[0].lower()!='multipart/form-data': raise Failure(415,'MULTIPART_REQUIRED')
    cap=storage.LIMIT+65536; received=0
    async def receive():
        nonlocal received
        msg=await request.receive(); received+=len(msg.get('body',b''))
        if received>cap: raise MultiPartException('Request too large')
        return msg
    bounded=Request(request.scope,receive)
    try:
        async with bounded.form(max_files=1,max_fields=3,max_part_size=4096) as form:
            if len(form.multi_items())!=len(form): raise Failure(422,'DUPLICATE_FORM_FIELD')
            f=form.get('file')
            if not isinstance(f,UploadFile) or not f.filename: raise Failure(422,'FILE_REQUIRED')
            link=Link.model_validate({k:v for k,v in form.items() if k!='file'})
            digest=sha256(); buf=bytearray()
            while chunk:=await f.read(65536):
                if len(buf)+len(chunk)>storage.LIMIT: raise Failure(413,'INVALID_EVIDENCE_SIZE')
                digest.update(chunk); buf.extend(chunk)
            return await run_in_threadpool(upload_write,authorization,projectId,key,int(expected),link,f.filename,f.content_type,bytes(buf),digest.hexdigest())
    except ValidationError: raise Failure(422,'INVALID_EVIDENCE_LINK') from None
    except (FormError,MultiPartException):
        if received>cap: raise Failure(413,'INVALID_EVIDENCE_SIZE') from None
        raise Failure(422,'INVALID_MULTIPART') from None


@router.get('/projects/{projectId}/evidence',operation_id='human_evidence_list',response_model_exclude_none=True)
def listing(projectId:UUID,authorization:Auth,page:int=Query(1,ge=1),pageSize:int=Query(20,ge=1,le=100),
            requirementId:UUID|None=None,verifiedOnly:bool=False)->Result[Page]:
    op='human_evidence_list'
    def read(c,a,_):
        project_access(c,a,projectId); reviewer=member(c,a,projectId,'reviewer')
        query=stmt().where(e.c.project_id==projectId)
        if requirementId is not None: query=query.where(links.c.resource_type=='requirement',links.c.resource_id==requirementId)
        if verifiedOnly: query=query.where(e.c.quarantine_status=='verified')
        total=c.scalar(sa.select(sa.func.count()).select_from(query.subquery()))
        count=c.scalar(sa.select(sa.func.count()).select_from(e).where(e.c.project_id==projectId))
        rows=c.execute(query.order_by(e.c.created_at.desc(),e.c.id).offset((page-1)*pageSize).limit(pageSize)).mappings()
        items=[item(r,reviewer and r.uploaded_by!=a.person and r.quarantine_status=='pending_review') for r in rows]
        return Result[Page](data=Page(items=items,total=total,page=page,pageSize=pageSize,collectionRevision=count+1),meta=meta(op,[i.evidenceRef for i in items]))
    return run(authorization,op,read)


@router.get('/evidence/{evidenceId}',operation_id='human_evidence_get',response_model=Result[Evidence],response_model_exclude_none=True,
    responses={200:{'content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}}})
def get(evidenceId:UUID,authorization:Auth,download:bool=False):
    op='human_evidence_get'
    def read(c,a,_):
        row=lookup(c,a,evidenceId)
        if download:
            if row.quarantine_status not in ('pending_review','verified'): raise Failure(403,'QUARANTINED')
            return Response(storage.checked_bytes(row.storage_key,row.sha256,row.size_bytes),media_type='application/octet-stream',headers={
                'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row.original_name,safe=''),'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})
        out=item(row); return Result[Evidence](data=out,meta=meta(op,[out.evidenceRef]))
    return run(authorization,op,read)


@router.post('/evidence/{evidenceId}/verify',operation_id='human_evidence_verify',response_model_exclude_none=True)
def verify(evidenceId:UUID,command:Verify,authorization:Auth,key:Key,expected:Revision)->Result[Evidence]:
    op='human_evidence_verify'; sig=fingerprint([str(evidenceId),int(expected),command.model_dump()])
    def write(c,a,rid):
        row=lookup(c,a,evidenceId,True)
        if row.uploaded_by==a.person or not member(c,a,row.project_id,'reviewer'): raise Failure(403,'INDEPENDENT_REVIEWER_REQUIRED')
        prior=replay(c,a,op,key,sig,Result[Evidence])
        if prior: return prior
        if row.revision!=int(expected): raise Failure(409,'VERSION_CONFLICT')
        if row.quarantine_status!='pending_review' or row.sha256!=command.sha256 or len(command.reason.strip())<4: raise Failure(422,'INVALID_VERIFICATION')
        storage.checked_bytes(row.storage_key,row.sha256,row.size_bytes)
        c.execute(e.update().where(e.c.id==row.id).values(verified_by=a.person,verified_at=sa.func.now(),quarantine_status='verified'))
        c.execute(links.update().where(links.c.evidence_id==row.id).values(revision=row.revision+1,verification_note=command.reason.strip()))
        out=item(lookup(c,a,row.id)); return finish(c,a,op,key,rid,row.project_id,sig,Result[Evidence](data=out,meta=meta(op,[out.evidenceRef])),fingerprint(item(row).model_dump(mode='json')))
    return run(authorization,op,write)


def verified_link(c,a,ref,pid,kind,resource_id):
    try: eid=UUID(ref.removeprefix('evidence:'))
    except ValueError: raise Failure(422,'INVALID_EVIDENCE_REF') from None
    row=lookup(c,a,eid,True)
    if row.project_id!=pid or row.resource_type!=kind or row.resource_id!=resource_id: raise Failure(422,'EVIDENCE_TARGET_MISMATCH')
    if row.quarantine_status!='verified' or not row.verified_at or not row.verified_by or row.verified_by==row.uploaded_by: raise Failure(422,'VERIFIED_EVIDENCE_REQUIRED')
    storage.checked_bytes(row.storage_key,row.sha256,row.size_bytes)
    return row


@router.post('/milestones/{milestoneId}/complete',operation_id='human_milestone_complete',response_model_exclude_none=True)
def complete(milestoneId:UUID,command:Complete,authorization:Auth,key:Key,expected:Revision)->Result[View]:
    op='human_milestone_complete'; sig=fingerprint([str(milestoneId),int(expected),command.model_dump(mode='json')])
    def write(c,a,rid):
        p,m=db.projects,db.milestones
        project=c.execute(sa.select(p).join(m,m.c.project_id==p.c.id).where(m.c.id==milestoneId,visible(a)).with_for_update(of=p)).mappings().one_or_none()
        if not project: raise Failure(404,'RESOURCE_NOT_FOUND')
        if project.pm_id!=a.person or not member(c,a,project.id,'pm'): raise Failure(403,'FORBIDDEN')
        prior=replay(c,a,op,key,sig,Result[View])
        if prior: return prior
        old=c.execute(sa.select(m).where(m.c.id==milestoneId).with_for_update()).mappings().one()
        if old.revision!=int(expected): raise Failure(409,'VERSION_CONFLICT')
        if project.stage not in ('executing','delivering') or old.actual_date or old.is_cancelled: raise Failure(422,'MILESTONE_LOCKED')
        if command.actualDate>c.scalar(sa.text("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date")): raise Failure(422,'FUTURE_ACTUAL_DATE')
        proof=verified_link(c,a,command.evidenceRef,project.id,'milestone',milestoneId)
        row=c.execute(m.update().where(m.c.id==milestoneId).values(actual_date=command.actualDate,evidence_ref=f'evidence:{proof.id}',
            revision=old.revision+1,updated_at=sa.func.now()).returning(m)).mappings().one()
        c.execute(sa.text('''INSERT INTO milestone_completions(milestone_id,evidence_id,completed_by,actual_date,reason,revision,request_id)
            VALUES(:mid,:eid,:person,:day,:reason,:rev,:rid)'''),dict(mid=milestoneId,eid=proof.id,person=a.person,day=row.actual_date,reason=command.reason,rev=row.revision,rid=UUID(rid)))
        reconcile(c,project,row); out=view(row)
        return finish(c,a,op,key,rid,project.id,sig,Result[View](data=out,meta=meta(op,[f'milestone:{row.id}@{row.revision}',row.evidence_ref])),fingerprint(view(old).model_dump(mode='json')))
    return run(authorization,op,write)
