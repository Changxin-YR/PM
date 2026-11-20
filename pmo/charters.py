from decimal import Decimal
import sqlalchemy as sa

from . import schema_v1 as db
from .charter_models import Draft, full, summary
from .contracts import CharterSummary, Result
from .identity import Failure
from .materials import finish, project_access, replay
from .projects import meta, run, fingerprint

COLUMNS={'valueProp':'value_prop','targetClient':'target_client','attractiveness':'attractiveness',
         'expectedBenefit':'expected_benefit','successCriteria':'success_criteria','roughCost':'rough_cost',
         'roughEffort':'rough_effort','roughDuration':'rough_duration_days','topRisks':'top_risks'}


def read(auth,pid):
    op='human_charter_get'
    def query(c,a,_):
        if a.kind!='human': raise Failure(403,'FORBIDDEN')
        project=project_access(c,a,pid)
        ch=db.charters
        row=c.execute(sa.select(ch).where(ch.c.project_id==pid)).mappings().one_or_none()
        data=Draft(expectedRevision=row.revision if row else project.revision,charter=full(row) if row else None)
        refs=[f'charter:{row.id}@{row.revision}'] if row else [f'project:{pid}@{project.revision}']
        return Result[Draft](data=data,meta=meta(op,refs))
    return run(auth,op,query)


def save(auth,pid,expected,key,command):
    op='pmo_charter_save'
    fields=command.fieldsCharter.model_dump(exclude_unset=True)
    signature=fingerprint({'projectId':str(pid),'expectedRevision':expected,'fields':fields})
    def write(c,a,rid):
        project=project_access(c,a,pid,edit=True)
        if project.stage!='drafting': raise Failure(422,'BASELINE_FROZEN')
        ch=db.charters
        row=c.execute(sa.select(ch).where(ch.c.project_id==pid).with_for_update()).mappings().one_or_none()
        if row and row.is_frozen: raise Failure(422,'BASELINE_FROZEN')
        replayed=replay(c,a,op,key,signature,Result[CharterSummary])
        if replayed: return replayed
        if expected!=(row.revision if row else project.revision): raise Failure(409,'VERSION_CONFLICT')
        values={COLUMNS[k]:Decimal(v) if k in ('roughCost','roughEffort') else v for k,v in fields.items()}
        before=fingerprint(full(row).model_dump(mode='json')) if row else None
        stmt=(ch.update().where(ch.c.id==row.id,ch.c.revision==expected).values(updated_at=sa.func.now())
              if row else ch.insert().values(project_id=pid,created_by=a.person))
        saved=c.execute(stmt.values(**values,revision=expected+1).returning(ch)).mappings().one()
        result=Result[CharterSummary](data=summary(saved),meta=meta(op,[f'charter:{saved.id}@{saved.revision}']))
        return finish(c,a,op,key,rid,pid,signature,result,before)
    return run(auth,op,write)
