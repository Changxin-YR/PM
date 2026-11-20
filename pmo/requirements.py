from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import Field, model_validator

from . import schema_v1 as db
from .contracts import Result, Strict
from .identity import Failure, visible
from .materials import finish, project_access, replay
from .projects import fingerprint, meta, run

Description = Annotated[str, Field(min_length=5,max_length=4000,pattern=r'\S')]
Priority = Literal['P0','P1','P2']


class Create(Strict):
    desc: Description
    priority: Priority
    acceptanceCriteria: Description
    plannedConfirmAt: date | None = None


class Fields(Strict):
    desc: Description | None = None
    priority: Priority | None = None
    acceptanceCriteria: Description | None = None
    plannedConfirmAt: date | None = None

    @model_validator(mode='after')
    def nonempty(self):
        if not self.model_fields_set or None in self.model_dump(exclude_unset=True).values():
            raise ValueError('Expected non-null changed fields')
        return self


class Update(Strict):
    fieldsRequirement: Fields


class View(Strict):
    requirementId: UUID
    projectId: UUID
    desc: str
    priority: Priority
    status: Literal['baseline','delivered','accepted','customer_confirmed','cancelled']
    baselineVersion: int
    revision: int
    acceptedAt: datetime | None = None
    acceptanceCriteria: str
    plannedConfirmAt: date | None = None
    sourceRefs: list[str]


class Page(Strict):
    deliveryActions: list[str] | None = None
    items: list[View]
    total: int
    page: int
    pageSize: int


def view(row):
    return View(requirementId=row.id,projectId=row.project_id,desc=row['desc'],priority=row.priority,
                status=row.status,baselineVersion=row.baseline_version,revision=row.revision,
                acceptedAt=row.accepted_at,acceptanceCriteria=row.acceptance_criteria,plannedConfirmAt=row.planned_confirm_at,
                sourceRefs=[f'requirement:{row.id}@{row.revision}'])


def listing(auth,pid,page,size,status,keyword):
    from .requirement_delivery import capabilities
    op='pmo_requirement_list'
    def query(c,a,_):
        project=project_access(c,a,pid)
        r=db.requirements
        predicates=[r.c.project_id==pid]
        if status: predicates.append(r.c.status==status)
        if keyword: predicates.append(r.c['desc'].icontains(keyword,autoescape=True))
        total=c.scalar(sa.select(sa.func.count()).select_from(r).where(*predicates))
        rows=c.execute(sa.select(r).where(*predicates).order_by(r.c.priority,r.c.created_at,r.c.id).limit(size).offset((page-1)*size)).mappings()
        items=[view(row) for row in rows]
        return Result[Page](data=Page(items=items,page=page,pageSize=size,total=total,deliveryActions=capabilities(c,a,project)),meta=meta(op,[i.sourceRefs[0] for i in items]))
    return run(auth,op,query)


def save(auth,key,command,*,pid=None,req_id=None,expected=None):
    creating=req_id is None
    op='pmo_requirement_create' if creating else 'pmo_requirement_draft_update'
    signature=fingerprint({'target':str(pid if creating else req_id),'revision':expected,'fields':command.model_dump(mode='json',exclude_unset=True)})
    def write(c,a,rid):
        r=db.requirements
        project_id=pid if creating else c.scalar(sa.select(r.c.project_id).join(db.projects,db.projects.c.id==r.c.project_id).where(r.c.id==req_id,visible(a)))
        project_access(c,a,project_id,edit=True)
        old=replay(c,a,op,key,signature,Result[View])
        if old: return old
        frozen=c.scalar(sa.select(r.c.id).where(r.c.project_id==project_id,r.c.baseline_version>0).limit(1))
        if frozen: raise Failure(422,'BASELINE_FROZEN')
        before=None
        if creating:
            stmt=r.insert().values(project_id=project_id,created_by=a.person,desc=command.desc,priority=command.priority,
                       acceptance_criteria=command.acceptanceCriteria,planned_confirm_at=command.plannedConfirmAt,status='baseline',baseline_version=0)
        else:
            row=c.execute(sa.select(r).where(r.c.id==req_id).with_for_update()).mappings().one()
            if row.status!='baseline': raise Failure(422,'BASELINE_FROZEN')
            if row.revision!=expected: raise Failure(409,'VERSION_CONFLICT')
            fields=command.fieldsRequirement.model_dump(exclude_unset=True)
            columns={'desc':'desc','priority':'priority','acceptanceCriteria':'acceptance_criteria','plannedConfirmAt':'planned_confirm_at'}
            before=fingerprint(view(row).model_dump(mode='json'))
            stmt=r.update().where(r.c.id==req_id,r.c.revision==expected).values(**{columns[k]:v for k,v in fields.items()},revision=expected+1,updated_at=sa.func.now())
        item=view(c.execute(stmt.returning(r)).mappings().one())
        return finish(c,a,op,key,rid,project_id,signature,Result[View](data=item,meta=meta(op,item.sourceRefs)),before)
    return run(auth,op,write)
