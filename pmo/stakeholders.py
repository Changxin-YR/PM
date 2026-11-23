from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .contracts import Result
from .identity import Actor, Browser, Failure
from .materials import finish, project_access, replay
from .pagination import pg_offset
from .projects import fingerprint, meta, run
from .schema_v1 import stakeholders as contacts
from .stakeholder_models import ContactDetails, ContactPage, ContactSummary, CreateContact


def create(auth: str | Browser | None, pid: UUID, command: CreateContact, key: str) -> Result[ContactSummary]:
    op = 'pmo_stakeholder_create'
    signature = fingerprint({'projectId': str(pid), **command.model_dump(exclude_unset=True)})

    def write(c: Connection, actor: Actor, request_id: str) -> Result[ContactSummary]:
        project_access(c, actor, pid, edit=True)
        old = replay(c, actor, op, key, signature, Result[ContactSummary])
        if old:
            return old
        stmt = contacts.insert().values(project_id=pid, created_by=actor.person, name=command.name,
            type=command.stakeholderType, influence=command.influence or None, expectation=command.expectation or None)
        item = ContactSummary.from_row(c.execute(stmt.returning(contacts)).mappings().one())
        return finish(c, actor, op, key, request_id, pid, signature,
                      Result[ContactSummary](data=item, meta=meta(op, [item.source_ref()])))

    return run(auth, op, write)


def listing(auth: str | Browser | None, pid: UUID, page: int, size: int) -> Result[ContactPage]:
    op = 'human_stakeholder_list'

    def query(c: Connection, actor: Actor, _: str) -> Result[ContactPage]:
        if actor.kind != 'human':
            raise Failure(403, 'FORBIDDEN')
        project_access(c, actor, pid)
        total = c.execute(sa.select(sa.func.count()).select_from(contacts).where(contacts.c.project_id == pid)).scalar_one()
        stmt = sa.select(contacts).where(contacts.c.project_id == pid).order_by(contacts.c.created_at.desc(), contacts.c.id)
        items = [ContactDetails(**ContactSummary.from_row(row).model_dump(), influence=row['influence'],
                                expectation=row['expectation']) for row in c.execute(stmt.limit(size).offset(pg_offset(page,size))).mappings()]
        return Result[ContactPage](data=ContactPage(items=items, total=total, page=page, pageSize=size),
                                   meta=meta(op, [item.source_ref() for item in items]))

    return run(auth, op, query)
