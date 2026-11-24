from datetime import date
from uuid import UUID

import sqlalchemy as sa
from pydantic import Field

from . import schema_v1 as db
from .contracts import RULE_VERSION, Result, Strict
from .identity import Failure, visible
from .health_schema import events
from .materials import finish, replay
from .milestones import View, refs, view
from .projects import fingerprint, meta, run
from .risks import WRITABLE, can_manage
from .rule_delivery import notify


class Forecast(Strict):
    newDate: date
    reason: str=Field(min_length=3,max_length=2000,pattern=r'\S')


def reconcile(c,p,row):
    e=events
    active=c.scalar(sa.text('SELECT event_id FROM milestone_delay_episodes WHERE milestone_id=:mid AND closing_revision IS NULL'),{'mid':row.id})
    late=(p.stage in ('executing','on_hold','delivering','closing_review') and row.actual_date is None
          and not row.is_cancelled and row.forecast_date is not None and row.forecast_date>row.planned_date)
    if active and not late:
        c.execute(sa.text('UPDATE milestone_delay_episodes SET closing_revision=:rev WHERE event_id=:id'),{'rev':row.revision,'id':active})
        c.execute(e.update().where(e.c.id==active).values(status='resolved',updated_at=sa.func.now()))
    if active and late:
        notify(c,p.id,active,p.pm_id)
        return active
    if not late: return None
    source=f'milestone:{row.id}@{row.revision}'
    journal=c.execute(sa.text('''SELECT revision,reason FROM milestone_forecasts WHERE milestone_id=:mid AND revision=:rev
        AND planned_date=:planned AND new_date=:forecast'''),dict(mid=row.id,rev=row.revision,planned=row.planned_date,
                                                                   forecast=row.forecast_date)).mappings().one_or_none()
    eid=c.scalar(e.insert().values(project_id=p.id,rule_id='LR-03',rule_version=RULE_VERSION,business_period=row.planned_date.isoformat(),
        fingerprint=fingerprint([source,RULE_VERSION]),status='open',source_refs=[source],
        input_snapshot={'milestoneName':row.name,'plannedDate':row.planned_date.isoformat(),
                        'forecastDate':row.forecast_date.isoformat(),'reason':journal.reason if journal else None}).returning(e.c.id))
    c.execute(sa.text('INSERT INTO milestone_delay_episodes(event_id,milestone_id,opening_revision) VALUES(:id,:mid,:rev)'),
              {'id':eid,'mid':row.id,'rev':journal.revision if journal else None})
    notify(c,p.id,eid,p.pm_id)
    return eid


def save(auth,mid,expected,key,command):
    op='pmo_milestone_forecast'; sig=fingerprint([str(mid),expected,command.model_dump(mode='json')])
    def write(c,a,rid):
        m,p=db.milestones,db.projects
        project=c.execute(sa.select(p).join(m,m.c.project_id==p.c.id).where(visible(a),m.c.id==mid).with_for_update(of=p)).mappings().one_or_none()
        if not project: raise Failure(404,'RESOURCE_NOT_FOUND')
        if not can_manage(c,a,project): raise Failure(403,'FORBIDDEN')
        old=replay(c,a,op,key,sig,Result[View])
        if old: return old
        row=c.execute(sa.select(m).where(m.c.id==mid,m.c.project_id==project.id).with_for_update()).mappings().one()
        if project.stage not in WRITABLE or row.is_cancelled or row.actual_date: raise Failure(422,'MILESTONE_LOCKED')
        if row.revision!=expected: raise Failure(409,'VERSION_CONFLICT')
        if len(command.reason.strip())<3: raise Failure(422,'INVALID_REASON')
        if row.forecast_date==command.newDate: raise Failure(422,'FORECAST_UNCHANGED')
        saved=c.execute(m.update().where(m.c.id==mid).values(forecast_date=command.newDate,revision=expected+1,updated_at=sa.func.now()).returning(m)).mappings().one()
        c.execute(sa.text('''INSERT INTO milestone_forecasts(milestone_id,revision,planned_date,prior_date,new_date,reason,created_by,request_id)
            VALUES(:mid,:rev,:plan,:prior,:new,:reason,:actor,:rid)'''),dict(mid=mid,rev=saved.revision,plan=row.planned_date,prior=row.forecast_date,
                new=saved.forecast_date,reason=command.reason.strip(),actor=a.person,rid=UUID(rid)))
        reconcile(c,project,saved)
        item=view(saved)
        return finish(c,a,op,key,rid,project.id,sig,Result[View](data=item,meta=meta(op,refs([item]))),fingerprint(view(row).model_dump(mode='json')))
    return run(auth,op,write)
