from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy.engine import RowMapping

from .dto import Strict

ContactType = Literal['customer', 'partner', 'internal']


class CreateContact(Strict):
    name: str = Field(min_length=1, max_length=160, pattern=r'\S')
    stakeholderType: ContactType
    influence: str = Field(default='', max_length=100)
    expectation: str = Field(default='', max_length=2000)


class ContactSummary(Strict):
    stakeholderId: UUID
    projectId: UUID
    name: str
    type: ContactType
    contactRole: str | None = None
    revision: int

    @classmethod
    def from_row(cls, row: RowMapping) -> 'ContactSummary':
        return cls(stakeholderId=row['id'], projectId=row['project_id'], name=row['name'],
                   type=row['type'], contactRole=row['contact_role'], revision=row['revision'])

    def source_ref(self) -> str:
        return f'stakeholder:{self.stakeholderId}@{self.revision}'


class ContactDetails(ContactSummary):
    influence: str | None = None
    expectation: str | None = None


class ContactPage(Strict):
    items: list[ContactDetails]
    total: int
    page: int
    pageSize: int
