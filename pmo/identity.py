from dataclasses import dataclass
from hmac import compare_digest
from hashlib import sha256
from uuid import UUID
import sqlalchemy as sa
from . import schema_v1 as db
from .session_schema import sessions
from .tool_routes import ROUTES

explicit_grants = sa.table('agent_project_grants', sa.column('credential_id', sa.Uuid),
                           sa.column('project_id', sa.Uuid), sa.column('granted_by', sa.Uuid))

class Failure(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code

@dataclass(frozen=True)
class Actor:
    credential: UUID
    person: UUID
    account: UUID
    org: UUID
    actions: frozenset[str]
    projects: tuple[UUID,...]
    create: bool
    kind: str = 'agent'

@dataclass(frozen=True)
class Browser:
    cookie: str
    csrf: str
    writing: bool

def browser_actor(conn,identity):
    s,p,u,o=sessions,db.persons,db.user_accounts,db.organizations
    digest=sha256(identity.cookie.encode()).hexdigest()
    row=conn.execute(sa.select(u.c.id,p.c.id.label('person'),p.c.org_id,s.c.csrf_hash).select_from(s.join(u,u.c.id==s.c.account_id).join(p,p.c.user_id==u.c.id).join(o,o.c.id==p.c.org_id)).where(
        s.c.token_hash==digest,s.c.revoked_at.is_(None),s.c.expires_at>sa.func.now(),p.c.is_active,u.c.is_active,o.c.status=='active',sa.or_(u.c.locked_until.is_(None),u.c.locked_until<=sa.func.now())).with_for_update(of=u)).mappings().one_or_none()
    if not row: raise Failure(401,'UNAUTHORIZED')
    if identity.writing and not compare_digest(sha256(identity.csrf.encode()).hexdigest(),row.csrf_hash): raise Failure(403,'CSRF_INVALID')
    return Actor(row.id,row.person,row.id,row.org_id,frozenset({*ROUTES,'auth_logout','auth_me','human_charter_get',
        'human_members_list','human_stakeholder_list','human_appointment_queue','human_my_role_invitations','human_issue_role_invitation',
        'human_accept_role_assignment','human_decline_role_assignment'}),(),True,'human')

def authenticate(conn, header: str | Browser | None) -> Actor:
    if isinstance(header,Browser): return browser_actor(conn,header)
    if not header or not header.startswith('Bearer ') or len(header)>1024:
        raise Failure(401,'UNAUTHORIZED')
    digest = sha256(header[7:].encode()).hexdigest()
    a,p,u,o=db.agent_credentials,db.persons,db.user_accounts,db.organizations
    query = sa.select(a,p.c.id.label('person')).select_from(a.join(u,u.c.id==a.c.delegated_by).join(p,p.c.user_id==u.c.id).join(o,o.c.id==a.c.org_id)).where(
        a.c.token_hash==digest,a.c.revoked_at.is_(None),a.c.status=='active',a.c.expires_at>sa.func.now(),
        p.c.org_id==a.c.org_id,p.c.is_active,u.c.is_active,o.c.status=='active',
        sa.or_(u.c.locked_until.is_(None),u.c.locked_until<=sa.func.now())).with_for_update(of=a)
    row=conn.execute(query).mappings().one_or_none()
    if row is None: raise Failure(401,'UNAUTHORIZED')
    return Actor(row.id,row.person,row.delegated_by,row.org_id,frozenset(row.allowed_actions),tuple(map(UUID,row.allowed_project_ids)),row.allow_project_create)

def visible(actor):
    p,m=db.projects,db.project_members
    accepted=(m.c.project_id==p.c.id,m.c.person_id==actor.person,m.c.assignment_status=='accepted',m.c.accepted_at.is_not(None),m.c.revoked_at.is_(None))
    member=sa.exists(sa.select(m.c.id).where(*accepted,m.c.role!='initiator'))
    own_draft=sa.and_(p.c.initiator_id==actor.person,p.c.stage=='drafting',sa.exists(sa.select(m.c.id).where(*accepted,m.c.role=='initiator')))
    if actor.kind=='agent':
        explicit=sa.exists(sa.select(explicit_grants.c.project_id).where(
            explicit_grants.c.credential_id==actor.credential,explicit_grants.c.project_id==p.c.id))
        return sa.and_(p.c.org_id==actor.org,p.c.id.in_(actor.projects),sa.or_(own_draft,sa.and_(explicit,member)))
    return sa.and_(p.c.org_id==actor.org,sa.or_(member,own_draft))
