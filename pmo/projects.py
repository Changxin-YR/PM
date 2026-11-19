from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from . import schema_v1 as db
from .contracts import Meta, Page, ProjectDetail, ProjectView, Result, RULE_VERSION
from .identity import Failure, authenticate, visible
from .db import engine

def view(row):
    return ProjectView(projectId=row.id, code=row.code, name=row.name, stage=row.stage, revision=row.revision, orgId=row.org_id, createdAt=row.created_at)

def meta(op, refs):
    return Meta(asOf=datetime.now(UTC),operationId=op,sourceRefs=refs)

def fingerprint(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def run(header,op,fn):
    actor=None
    request_id=str(uuid4())
    try:
        with engine().begin() as c:
            actor=authenticate(c,header)
            if op not in actor.actions: raise Failure(403,'FORBIDDEN')
            return fn(c,actor,request_id)
    except Failure as e:
        if actor:
            with engine().begin() as c:
                c.execute(db.audit_logs.insert().values(action=op,actor_type='agent',actor_id=actor.credential,delegated_by=actor.account,org_id=actor.org,result='denied',error_code=e.code,request_id=request_id,rule_version=RULE_VERSION))
        raise

def listing(header,page,size,stage,keyword):
    op='pmo_project_list'
    def query(c,a,_):
        p=db.projects
        filters=[visible(a)]
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
        row=c.execute(sa.select(db.projects).where(visible(a),db.projects.c.id==pid)).mappings().one_or_none()
        if not row: raise Failure(404,'RESOURCE_NOT_FOUND')
        return Result[ProjectDetail](data=ProjectDetail(project=view(row)),meta=meta(op,[f'project:{pid}@{row.revision}']))
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
        c.execute(db.agent_credentials.update().where(db.agent_credentials.c.id==a.credential).values(allowed_project_ids=[str(i) for i in (*a.projects,row.id)]))
        result=Result[ProjectView](data=view(row),meta=meta(op,[f'project:{row.id}@1']))
        response=result.model_dump(mode='json'); digest=fingerprint(response)
        c.execute(idem.insert().values(actor_id=a.credential,actor_type='agent',org_id=a.org,delegated_by=a.person,idempotency_key=key,operation=op,completed_at=sa.func.now(),expires_at=datetime.now(UTC)+timedelta(days=30),request_hash=payload_hash,response_body=response,response_hash=digest,response_status=200,status='completed',resource_id=row.id))
        c.execute(db.audit_logs.insert().values(action=op,actor_type='agent',actor_id=a.credential,delegated_by=a.account,org_id=a.org,project_id=row.id,after_hash=digest,result='success',request_id=request_id,idempotency_key=key,rule_version=RULE_VERSION))
        c.execute(db.outbox_events.insert().values(dedupe_key=request_id,event_type='project.created',payload={'projectId':str(row.id)},status='pending'))
        return result
    return run(header,op,write)
