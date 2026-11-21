from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID
from pydantic import Field, PrivateAttr, model_validator
from .dto import Strict as Strict
from .stakeholder_models import ContactSummary

RULE_VERSION = 'lite-v1.0-standalone.3'

class CreateProject(Strict):
    code: str = Field(min_length=2,max_length=60,pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
    name: str = Field(min_length=1,max_length=160,pattern=r'\S')
    description: str = Field(default='',max_length=2000)

class ProjectView(Strict):
    projectId: UUID
    code: str
    name: str
    stage: Literal['drafting','charter_review','rejected','designing','feasibility_review','executing','on_hold','delivering','closing_review','closed','retrospective','terminated','archived']
    revision: int
    orgId: UUID
    createdAt: datetime

class DraftFields(Strict):
    name: str | None = Field(default=None,min_length=1,max_length=160,pattern=r'\S')
    description: str | None = Field(default=None,max_length=2000)

    @model_validator(mode='after')
    def valid_changes(self):
        if not self.model_fields_set or any(getattr(self,k) is None for k in self.model_fields_set):
            raise ValueError('At least one non-null draft field is required')
        return self

class UpdateDraft(Strict):
    fields: DraftFields

class Page(Strict):
    items: list[ProjectView]
    page: int
    pageSize: int
    total: int

class CharterSummary(Strict):
    charterId: UUID
    projectId: UUID
    revision: int
    isFrozen: bool
    valueProp: str | None = None
    targetClient: str | None = None
    roughCost: str | None = None
    roughEffort: str | None = None
    roughDurationDays: int | None = None

class ProjectDetail(Strict):
    project: ProjectView
    charterSummary: CharterSummary | None = None
    stakeholders: list[ContactSummary] = Field(max_length=100)

class Meta(Strict):
    asOf: datetime
    sourceRefs: list[str]
    ruleVersion: str = RULE_VERSION
    operationId: str
    _audit_project_id: UUID | None = PrivateAttr(default=None)

T = TypeVar('T')
class Result(Strict, Generic[T]):
    data: T
    meta: Meta
