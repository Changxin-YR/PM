from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID
import sqlalchemy as sa
from . import schema_v1 as db

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

def authenticate(conn, header: str | None) -> Actor:
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
    member=sa.exists(sa.select(m.c.id).where(m.c.project_id==p.c.id,m.c.person_id==actor.person,m.c.assignment_status=='accepted',m.c.revoked_at.is_(None)))
    return sa.and_(p.c.org_id==actor.org,p.c.id.in_(actor.projects),sa.or_(member,sa.and_(p.c.initiator_id==actor.person,p.c.stage=='drafting')))
