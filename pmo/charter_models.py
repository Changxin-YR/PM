from decimal import Decimal
from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from .contracts import CharterSummary, Strict

Text = Annotated[str, Field(min_length=1, max_length=3000, pattern=r'\S')]
Client = Annotated[str, Field(min_length=1, max_length=2000, pattern=r'\S')]
Money = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$')]
Effort = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$')]
Days = Annotated[StrictInt, Field(ge=1, le=3650)]


class TopRisk(Strict):
    statement: Text
    response: Text


class Fields(Strict):
    valueProp: Text | None = None
    targetClient: Client | None = None
    attractiveness: Text | None = None
    expectedBenefit: Text | None = None
    successCriteria: Text | None = None
    roughCost: Money | None = None
    roughEffort: Effort | None = None
    roughDuration: Days | None = None
    topRisks: Annotated[list[TopRisk], Field(min_length=1, max_length=100)] | None = None

    @model_validator(mode='after')
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError('Non-empty charter changes with non-null values required')
        return self


class Save(Strict):
    fieldsCharter: Fields


class Full(CharterSummary):
    attractiveness: str | None = None
    expectedBenefit: str | None = None
    successCriteria: str | None = None
    topRisks: list[TopRisk] | None = None


class Draft(Strict):
    expectedRevision: int
    charter: Full | None = None


def summary(row):
    def number(v: Decimal | None): return format(v, 'f') if v is not None else None
    return CharterSummary(charterId=row.id,projectId=row.project_id,revision=row.revision,isFrozen=row.is_frozen,
                         valueProp=row.value_prop,targetClient=row.target_client,roughCost=number(row.rough_cost),
                         roughEffort=number(row.rough_effort),roughDurationDays=row.rough_duration_days)


def full(row):
    return Full(**summary(row).model_dump(),attractiveness=row.attractiveness,expectedBenefit=row.expected_benefit,
                successCriteria=row.success_criteria,topRisks=row.top_risks)
