"""A proposal never grants membership. Only the nominee's authenticated acceptance does."""
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from . import schema_v1 as db
from .contracts import Result, Strict
from .identity import Failure
from .materials import finish, project_access, replay
from .projects import fingerprint, meta, run

Role = Literal['sponsor', 'pm']
authority = sa.table('appointment_authorities', *(sa.column(n, sa.Uuid) for n in ('org_id','person_id')),
                     sa.column('role'), sa.column('expires_at'), sa.column('revoked_at'))
inv = sa.table('role_invitations', *(sa.column(n, sa.Uuid) for n in ('id','project_id','person_id','proposer_id','issuer_id')),
               *(sa.column(n) for n in ('role','status','revision','expires_at','decided_at','created_at')))


class Proposal(Strict):
    personId: UUID
    role: Role


class Invitation(Proposal):
    invitationId: UUID
    projectId: UUID
    status: str
    expiresAt: datetime | None = None


class InboxItem(Invitation):
    revision: int
    projectName: str
    nomineeName: str


def view(row):
    return Invitation(invitationId=row.id,projectId=row.project_id,personId=row.person_id,
                      status=row.status,role=row.role,expiresAt=row.expires_at)


def result(row,op):
    return Result[Invitation](data=view(row),meta=meta(op,[f'role_invitation:{row.id}@{row.revision}']))


def eligible_person(c,pid,org):
    p,u=db.persons,db.user_accounts
    return c.scalar(sa.select(p.c.id).select_from(p.join(u,u.c.id==p.c.user_id)).where(
        p.c.id==pid,p.c.org_id==org,p.c.is_active,u.c.is_active).with_for_update(read=True,of=p))


def propose(auth,pid,expected,key,command):
    op='pmo_role_assignment_propose'
    sig=fingerprint([str(pid),expected,command.model_dump(mode='json')])
    def write(c,a,rid):
        project=project_access(c,a,pid,edit=True)
        if project.stage!='drafting' or project.initiator_id!=a.person: raise Failure(403,'FORBIDDEN')
        cached=replay(c,a,op,key,sig,Result[Invitation])
        if cached: return cached
        if project.revision!=expected: raise Failure(409,'VERSION_CONFLICT')
        if not eligible_person(c,command.personId,a.org): raise Failure(404,'RESOURCE_NOT_FOUND')
        if project[command.role+'_id'] is not None: raise Failure(409,'ROLE_OCCUPIED')
        same=c.scalar(sa.select(inv.c.id).where(inv.c.project_id==pid,inv.c.person_id==command.personId,
            inv.c.role==command.role,sa.or_(inv.c.status=='proposed',sa.and_(inv.c.status=='invited',inv.c.expires_at>sa.func.now()))))
        if same: raise Failure(409,'INVITATION_EXISTS')
        row=c.execute(inv.insert().values(project_id=pid,person_id=command.personId,proposer_id=a.person,role=command.role,
                                         status='proposed').returning(*inv.c)).mappings().one()
        c.execute(db.projects.update().where(db.projects.c.id==pid).values(revision=expected+1,updated_at=sa.func.now()))
        return finish(c,a,op,key,rid,pid,sig,result(row,op))
    return run(auth,op,write)


def authority_scope(a,role):
    return (authority.c.org_id==a.org,authority.c.person_id==a.person,authority.c.role==role,
            authority.c.revoked_at.is_(None),authority.c.expires_at>sa.func.now())


def inbox(auth,for_issue=False):
    op='human_appointment_queue' if for_issue else 'human_my_role_invitations'
    def read(c,a,rid):
        if a.kind!='human': raise Failure(403,'FORBIDDEN')
        p,n=db.projects,db.persons
        rows=c.execute(sa.select(*inv.c,p.c.name.label('project_name'),n.c.name.label('nominee_name')).select_from(
            inv.join(p,p.c.id==inv.c.project_id).join(n,n.c.id==inv.c.person_id)).where(p.c.org_id==a.org,n.c.org_id==a.org,n.c.is_active,
            p.c.stage=='drafting',sa.and_(inv.c.status=='proposed',inv.c.person_id!=a.person,inv.c.proposer_id!=a.person,
            sa.exists(sa.select(1).select_from(authority).where(*authority_scope(a,inv.c.role)))) if for_issue else
            sa.and_(inv.c.person_id==a.person,inv.c.status=='invited',inv.c.expires_at>sa.func.now())).order_by(inv.c.created_at,inv.c.id)).mappings().all()
        return Result[list[InboxItem]](data=[InboxItem(**view(r).model_dump(),revision=r.revision,projectName=r.project_name,nomineeName=r.nominee_name) for r in rows],
                                      meta=meta(op,[f'role_invitation:{r.id}@{r.revision}' for r in rows]))
    return run(auth,op,read)


def respond(auth,iid,expected,key,action):
    op={'issue':'human_issue_role_invitation','accept':'human_accept_role_assignment','decline':'human_decline_role_assignment'}[action]
    sig=fingerprint([str(iid),expected,action])
    def write(c,a,rid):
        if a.kind!='human': raise Failure(403,'FORBIDDEN')
        p=db.projects
        query=sa.select(*inv.c,p.c.stage,p.c.pm_id,p.c.sponsor_id).select_from(inv.join(p,p.c.id==inv.c.project_id)).where(inv.c.id==iid,p.c.org_id==a.org)
        if action!='issue': query=query.where(inv.c.person_id==a.person)
        row=c.execute(query.with_for_update(of=[p,inv])).mappings().one_or_none()
        if not row: raise Failure(404,'RESOURCE_NOT_FOUND')
        if action=='issue':
            if a.person in (row.proposer_id,row.person_id) or not c.scalar(sa.select(authority.c.person_id).where(*authority_scope(a,row.role)).with_for_update()):
                raise Failure(403,'FORBIDDEN')
        cached=replay(c,a,op,key,sig,Result[Invitation])
        if cached: return cached
        if row.revision!=expected: raise Failure(409,'VERSION_CONFLICT')
        if row.stage!='drafting' or row.status!=('proposed' if action=='issue' else 'invited'): raise Failure(409,'INVITATION_NOT_ACTIONABLE')
        if not eligible_person(c,row.person_id,a.org): raise Failure(404,'RESOURCE_NOT_FOUND')
        if action!='decline' and row[row.role+'_id'] is not None: raise Failure(409,'ROLE_OCCUPIED')
        values={'revision':expected+1}
        now=datetime.now(UTC)
        if action=='issue':
            values.update(status='invited',issuer_id=a.person,expires_at=now+timedelta(days=7))
        else:
            if row.expires_at<=now: raise Failure(409,'INVITATION_EXPIRED')
            values.update(status='accepted' if action=='accept' else 'declined',decided_at=now)
            if action=='accept':
                # A revoked or expired signer's mandate cannot activate an outstanding invite.
                if not c.scalar(sa.select(authority.c.person_id).where(authority.c.org_id==a.org,authority.c.person_id==row.issuer_id,
                    authority.c.role==row.role,authority.c.revoked_at.is_(None),authority.c.expires_at>sa.func.now()).with_for_update()): raise Failure(403,'AUTHORITY_INACTIVE')
                members=db.project_members
                prior=c.scalar(sa.select(members.c.id).where(members.c.project_id==row.project_id,members.c.role==row.role,
                    members.c.assignment_status=='accepted',members.c.revoked_at.is_(None)))
                if prior: raise Failure(409,'ROLE_OCCUPIED')
                from sqlalchemy.dialects.postgresql import insert
                member_values=dict(assigned_by=row.issuer_id,assignment_status='accepted',accepted_at=now,
                    invitation_decision_at=now,invitation_expires_at=row.expires_at,revoked_at=None,updated_at=now)
                c.execute(insert(members).values(project_id=row.project_id,person_id=a.person,role=row.role,**member_values).on_conflict_do_update(
                    index_elements=['project_id','person_id','role'],set_=member_values))
                c.execute(p.update().where(p.c.id==row.project_id).values(**{row.role+'_id':a.person},revision=p.c.revision+1,updated_at=now))
        updated=c.execute(inv.update().where(inv.c.id==iid).values(**values).returning(*inv.c)).mappings().one()
        return finish(c,a,op,key,rid,row.project_id,sig,result(updated,op),fingerprint(view(row).model_dump(mode='json')))
    return run(auth,op,write)
