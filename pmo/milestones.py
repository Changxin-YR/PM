from datetime import date
from typing import Annotated
from uuid import UUID
from pydantic import Field, StrictBool, StrictInt, model_validator
import sqlalchemy as sa

from . import schema_v1 as db
from .contracts import Strict, Result
from .identity import Failure, visible
from .materials import project_access, replay, finish
from .projects import run, meta, fingerprint
from .risks import WRITABLE, can_manage

Name = Annotated[str, Field(min_length=1, max_length=160, pattern=r'\S')]
Ordinal = Annotated[StrictInt, Field(ge=1, le=8)]


class Create(Strict):
    name: Name
    plannedDate: date
    forecastDate: date | None = None
    isKey: StrictBool = False
    ordinal: Ordinal | None = None


class Fields(Strict):
    name: Name | None = None
    plannedDate: date | None = None
    forecastDate: date | None = None
    isKey: StrictBool | None = None
    ordinal: Ordinal | None = None

    @model_validator(mode='after')
    def validate_fields(self):
        if not self.model_fields_set or None in self.model_dump(exclude_unset=True).values():
            raise ValueError('Non-empty changes with non-null values required')
        return self


class Update(Strict):
    fieldsMilestone: Fields


class View(Strict):
    milestoneId: UUID
    projectId: UUID
    revision: int
    name: str
    plannedDate: date
    isKey: bool
    forecastDate: date | None = None
    actualDate: date | None = None
    evidenceRefs: list[str]


class Page(Strict):
    page: int = 1
    pageSize: int = 8
    total: int
    items: list[View]
    canDraft: bool | None=None
    canForecast: bool | None=None


def view(row):
    return View(milestoneId=row.id, projectId=row.project_id, revision=row.revision, name=row.name,
                plannedDate=row.planned_date, isKey=row.is_key, forecastDate=row.forecast_date,
                actualDate=row.actual_date, evidenceRefs=[row.evidence_ref] if row.evidence_ref else [])


def refs(items):
    return [f'milestone:{m.milestoneId}@{m.revision}' for m in items]


def listing(auth, pid):
    op = 'pmo_milestone_list'
    def query(c, a, _):
        project=project_access(c,a,pid)
        m=db.milestones
        rows=c.execute(sa.select(m).where(m.c.project_id==pid, ~m.c.is_cancelled).order_by(m.c.ordinal)).mappings()
        items=[view(r) for r in rows]
        manage=can_manage(c,a,project) if a.kind=='human' else None
        return Result[Page](data=Page(items=items,total=len(items),canDraft=manage and project.stage in ('drafting','designing'),
            canForecast=manage and project.stage in WRITABLE),meta=meta(op,refs(items)))
    return run(auth,op,query)


def save(auth, key, command, *, pid=None, mid=None, expected=None):
    creating=mid is None
    op='pmo_milestone_create' if creating else 'pmo_milestone_draft_update'
    signature=fingerprint({'target':str(pid if creating else mid), 'revision':expected,
                          'fields':command.model_dump(mode='json',exclude_unset=True)})
    def write(c,a,rid):
        m=db.milestones
        project_id=pid if creating else c.scalar(sa.select(m.c.project_id).join(db.projects,db.projects.c.id==m.c.project_id).where(m.c.id==mid, visible(a)))
        project_access(c,a,project_id,edit=True)
        old=replay(c,a,op,key,signature,Result[View])
        if old: return old
        slots=dict(c.execute(sa.select(m.c.ordinal,m.c.id).where(m.c.project_id==project_id,~m.c.is_cancelled)).all())
        before=None
        if creating:
            if len(slots)>=8: raise Failure(422,'MILESTONE_LIMIT')
            slot=command.ordinal or next(n for n in range(1,9) if n not in slots)
            if slot in slots: raise Failure(409,'ORDINAL_CONFLICT')
            stmt=m.insert().values(project_id=project_id,created_by=a.person,name=command.name,is_key=command.isKey,
                                   ordinal=slot,planned_date=command.plannedDate,forecast_date=command.forecastDate)
        else:
            row=c.execute(sa.select(m).where(m.c.id==mid,~m.c.is_cancelled).with_for_update()).mappings().one_or_none()
            if not row: raise Failure(404,'RESOURCE_NOT_FOUND')
            if expected!=row.revision: raise Failure(409,'VERSION_CONFLICT')
            fields=command.fieldsMilestone.model_dump(exclude_unset=True)
            if fields.get('ordinal') in slots and slots[fields['ordinal']]!=mid: raise Failure(409,'ORDINAL_CONFLICT')
            columns={'name':'name','plannedDate':'planned_date','forecastDate':'forecast_date','ordinal':'ordinal','isKey':'is_key'}
            before=fingerprint({**view(row).model_dump(mode='json'), 'ordinal':row.ordinal})
            stmt=m.update().where(m.c.id==mid,m.c.revision==expected).values(**{columns[k]:v for k,v in fields.items()},revision=expected+1,updated_at=sa.func.now())
        result=view(c.execute(stmt.returning(m)).mappings().one())
        return finish(c,a,op,key,rid,project_id,signature,Result[View](data=result,meta=meta(op,refs([result]))),before)
    return run(auth,op,write)
