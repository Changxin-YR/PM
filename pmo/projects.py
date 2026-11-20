from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from . import schema_v1 as db
from .contracts import Meta, Page, ProjectDetail, ProjectView, Result, RULE_VERSION
from .identity import Failure, authenticate, visible
from .db import engine
from .charter_models import summary as charter_summary
from .stakeholder_models import ContactSummary
from .tool_routes import ROUTES
from .request_context import correlation_id

def view(row):
    return ProjectView(projectId=row.id, code=row.code, name=row.name, stage=row.stage, revision=row.revision, orgId=row.org_id, createdAt=row.created_at)

def meta(op, refs):
    return Meta(asOf=datetime.now(UTC),operationId=op,sourceRefs=refs)

def fingerprint(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def run(header,op,fn):
    actor=None
    request_id=correlation_id()
    try:
        with engine().begin() as c:
            actor=authenticate(c,header)
            if op not in actor.actions or (actor.kind=='agent' and op not in ROUTES): raise Failure(403,'FORBIDDEN')
            return fn(c,actor,request_id)
    except Failure as e:
        actor=actor or e.actor
        if actor:
            with engine().begin() as c:
                c.execute(db.audit_logs.insert().values(action=op,actor_type=actor.kind,actor_id=actor.credential,delegated_by=actor.account if actor.kind=='agent' else None,org_id=actor.org,project_id=e.project_id,result='denied',error_code=e.code,request_id=request_id,rule_version=RULE_VERSION))
        raise

def listing(header,page,size,stage,keyword):
    op='pmo_project_list'
    def query(c,a,_):
        p=db.projects
        filters=[visible(a,include_initiator=True)]
        if stage: filters.append(p.c.stage==stage)
        if keyword: filters.append(sa.or_(p.c.name.icontains(keyword,autoescape=True),p.c.code.icontains(keyword,autoescape=True)))
        total=c.scalar(sa.select(sa.func.count()).select_from(p).where(*filters))
        rows=c.execute(sa.select(p).where(*filters).order_by(p.c.created_at.desc(),p.c.id).offset((page-1)*size).limit(size)).mappings()
        items=[view(row) for row in rows]
        return Result[Page](data=Page(items=items,page=page,pageSize=size,total=total),meta=meta(op,[f'project:{i.projectId}@{i.revision}' for i in items]))
    return run(header,op,query)

def detail(header,pid):
    op='pmo_project_get'
    def query(c,a,_):
        row=c.execute(sa.select(db.projects,visible(a).label('materials_visible')).where(
            visible(a,include_initiator=True),db.projects.c.id==pid)).mappings().one_or_none()
        if not row: raise Failure(404,'RESOURCE_NOT_FOUND')
        if not row.materials_visible:
            return Result[ProjectDetail](data=ProjectDetail(project=view(row),stakeholders=[]),meta=meta(op,[f'project:{pid}@{row.revision}']))
        charter=c.execute(sa.select(db.charters).where(db.charters.c.project_id==pid)).mappings().one_or_none()
        refs=[f'project:{pid}@{row.revision}']
        if charter: refs.append(f'charter:{charter.id}@{charter.revision}')
        s=db.stakeholders
        contacts=[ContactSummary.from_row(item) for item in c.execute(sa.select(
            s.c.id,s.c.project_id,s.c.name,s.c.type,s.c.contact_role,s.c.revision
        ).where(s.c.project_id==pid).order_by(s.c.created_at.desc(),s.c.id).limit(100)).mappings()]
        refs.extend(item.source_ref() for item in contacts)
        return Result[ProjectDetail](data=ProjectDetail(project=view(row),charterSummary=charter_summary(charter) if charter else None,stakeholders=contacts),meta=meta(op,refs))
    return run(header,op,query)

def create(header,command,key):
    op='pmo_project_create'
    def write(c,a,request_id):
        if not a.create: raise Failure(403,'FORBIDDEN')
        idem=db.idempotency_keys
        payload_hash=fingerprint(command.model_dump())
        old=c.execute(sa.select(idem).where(idem.c.actor_id==a.credential,idem.c.operation==op,idem.c.idempotency_key==key)).mappings().one_or_none()
        if old:
            if old.request_hash!=payload_hash: raise Failure(409,'IDEMPOTENCY_CONFLICT')
            if not c.scalar(sa.select(db.projects.c.id).where(visible(a),db.projects.c.id==old.resource_id)): raise Failure(403,'FORBIDDEN')
            return Result[ProjectView].model_validate(old.response_body)
        row=c.execute(insert(db.projects).values(**command.model_dump(),org_id=a.org,created_by=a.person,initiator_id=a.person,revision=1,stage='drafting').on_conflict_do_nothing(index_elements=['org_id','code']).returning(db.projects)).mappings().one_or_none()
        if not row: raise Failure(409,'PROJECT_CODE_CONFLICT')
        if a.kind=='agent':
            c.execute(db.agent_credentials.update().where(db.agent_credentials.c.id==a.credential).values(allowed_project_ids=[str(i) for i in (*a.projects,row.id)]))
        result=Result[ProjectView](data=view(row),meta=meta(op,[f'project:{row.id}@1']))
        response=result.model_dump(mode='json'); digest=fingerprint(response)
        record_write(c,a,key,op,request_id,row.id,payload_hash,response,digest,'project.created')
        return result
    return run(header,op,write)

def record_receipt(c,a,key,op,request_id,resource_id,pid,payload_hash,response,digest,before=None):
    c.execute(db.idempotency_keys.insert().values(actor_id=a.credential,actor_type=a.kind,org_id=a.org,delegated_by=a.person if a.kind=='agent' else None,idempotency_key=key,operation=op,completed_at=sa.func.now(),expires_at=datetime.now(UTC)+timedelta(days=90),request_hash=payload_hash,response_body=response,response_hash=digest,response_status=200,status='completed',resource_id=resource_id))
    c.execute(db.audit_logs.insert().values(action=op,actor_type=a.kind,actor_id=a.credential,delegated_by=a.account if a.kind=='agent' else None,org_id=a.org,project_id=pid,before_hash=before,after_hash=digest,result='success',request_id=request_id,idempotency_key=key,rule_version=RULE_VERSION))

def record_write(c,a,key,op,request_id,pid,payload_hash,response,digest,event,before=None):
    record_receipt(c,a,key,op,request_id,pid,pid,payload_hash,response,digest,before)
    c.execute(db.outbox_events.insert().values(dedupe_key=request_id,event_type=event,payload={'projectId':str(pid)},status='pending'))

def update_draft(header,pid,expected,command,key):
    op='pmo_project_draft_update'
    fields=command.fields.model_dump(exclude_unset=True)
    payload_hash=fingerprint({'projectId':str(pid),'fields':fields,'expectedRevision':expected})
    def write(c,a,request_id):
        project=db.projects
        row=c.execute(sa.select(project).where(visible(a),project.c.id==pid).with_for_update()).mappings().one_or_none()
        if not row: raise Failure(404,'RESOURCE_NOT_FOUND')
        if row.initiator_id!=a.person or row.stage!='drafting': raise Failure(403,'FORBIDDEN')
        idem=db.idempotency_keys
        old=c.execute(sa.select(idem).where(idem.c.actor_id==a.credential,idem.c.operation==op,idem.c.idempotency_key==key)).mappings().one_or_none()
        if old:
            if old.request_hash!=payload_hash: raise Failure(409,'IDEMPOTENCY_CONFLICT')
            return Result[ProjectView].model_validate(old.response_body)
        if expected!=row.revision: raise Failure(409,'VERSION_CONFLICT')
        updated=c.execute(project.update().where(project.c.id==pid,project.c.revision==expected).values(**fields,revision=expected+1,updated_at=sa.func.now()).returning(project)).mappings().one()
        result=Result[ProjectView](data=view(updated),meta=meta(op,[f'project:{pid}@{updated.revision}']))
        response=result.model_dump(mode='json')
        record_write(c,a,key,op,request_id,pid,payload_hash,response,fingerprint(response),'project.updated',before=fingerprint({'name':row.name,'description':row.description,'revision':row.revision}))
        return result
    return run(header,op,write)
