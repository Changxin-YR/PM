"""Report field contracts, shared by HTTP, stdio and the deterministic weekly draft."""
import json
import re
from decimal import Decimal
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import AfterValidator, ConfigDict, Field, JsonValue, StrictStr, ValidationError, model_validator
from pydantic.json_schema import SkipJsonSchema

from .dto import Strict

UUID_PATTERN = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
ClaimKind = Literal['progress', 'completion']
Effort = Annotated[StrictStr, Field(pattern=r'^(0|[1-9][0-9]{0,9})(\.[0-9]{1,2})?$',
                                    json_schema_extra={'not': {'pattern': '[^0-9.]'}})]
ResourceId = Annotated[StrictStr, Field(pattern=f'^{UUID_PATTERN}$', max_length=36)]
EvidenceRef = Annotated[StrictStr, Field(pattern=f'^evidence:{UUID_PATTERN}$', max_length=45)]
EvidenceRefs = Annotated[list[EvidenceRef], Field(max_length=8)]


def _plain(value: str) -> str:
    if '\x00' in value:
        raise ValueError('Text must not contain NUL')
    value.encode('utf-8')
    return value


Text = Annotated[StrictStr, Field(max_length=20000), AfterValidator(_plain)]
EntryText = Annotated[StrictStr, Field(max_length=65536), AfterValidator(_plain)]


def uuid_value(value: JsonValue) -> UUID:
    if not isinstance(value, str) or not re.fullmatch(UUID_PATTERN, value):
        raise ValueError('Expected a resource UUID')
    return UUID(value)


def evidence_ids(item: dict[str, JsonValue]) -> tuple[UUID, ...]:
    raw = item.get('evidenceRefs', [])
    if not isinstance(raw, list) or len(raw) > 8:
        raise ValueError('Expected up to eight evidence references')
    refs = [*raw, item['evidenceRef']] if 'evidenceRef' in item else raw
    ids = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.startswith('evidence:'):
            raise ValueError('Expected an evidence reference')
        ids.append(uuid_value(ref[len('evidence:'):]))
    return tuple(dict.fromkeys(ids))


def _check_size(value: dict) -> None:
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 65536:
        raise ValueError('Report sections are too large')


def _source_dependencies(resource_key: str, *, milestone: bool = False):
    dependencies = {'evidenceRef': [resource_key]}
    if milestone:
        dependencies['claim'] = [resource_key]
    return {'dependentRequired': dependencies,
            'if': {'required': ['evidenceRefs'], 'properties': {'evidenceRefs': {'minItems': 1}}},
            'then': {'required': [resource_key]}}


class _Entry(Strict):
    resource_key: ClassVar[str]
    summary: EntryText | SkipJsonSchema[None] = None
    name: EntryText | SkipJsonSchema[None] = None
    description: EntryText | SkipJsonSchema[None] = None
    evidenceRef: EvidenceRef | SkipJsonSchema[None] = None
    evidenceRefs: EvidenceRefs | SkipJsonSchema[None] = None

    @model_validator(mode='after')
    def validated(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError('Omit optional report entry fields rather than passing null')
        if (self.evidenceRef or self.evidenceRefs or getattr(self, 'claim', None)) and not getattr(self, self.resource_key):
            raise ValueError('Claims and evidence references need the matching resource ID')
        return self


class MilestoneEntry(_Entry):
    resource_key = 'milestoneId'
    model_config = ConfigDict(json_schema_extra=_source_dependencies(resource_key, milestone=True))
    milestoneId: ResourceId | SkipJsonSchema[None] = None
    claim: ClaimKind | SkipJsonSchema[None] = None


class RequirementEntry(_Entry):
    resource_key = 'requirementId'
    model_config = ConfigDict(json_schema_extra=_source_dependencies(resource_key))
    requirementId: ResourceId | SkipJsonSchema[None] = None


class RiskEntry(_Entry):
    resource_key = 'riskId'
    model_config = ConfigDict(json_schema_extra=_source_dependencies(resource_key))
    riskId: ResourceId | SkipJsonSchema[None] = None


Milestones = Annotated[list[MilestoneEntry], Field(max_length=100)]
Requirements = Annotated[list[RequirementEntry], Field(max_length=100)]
Risks = Annotated[list[RiskEntry], Field(max_length=100)]
EffortCategory = Literal['development', 'implementation', 'management']


class EffortEntry(Strict):
    category: EffortCategory | Literal['unclassified']
    personDays: Effort
    basis: Annotated[Text, Field(min_length=1, max_length=1000, pattern=r'\S')]


EffortBreakdown = Annotated[list[EffortEntry], Field(min_length=1, max_length=4)]


def _check_effort(total: str | None, parts: list[EffortEntry] | None) -> None:
    if parts is None:
        return
    if total is None or sum(Decimal(item.personDays) for item in parts) != Decimal(total):
        raise ValueError('人天分类合计必须等于本期申报总量')
    if len({item.category for item in parts}) != len(parts):
        raise ValueError('同一人天类别只能申报一项')


class DraftFields(Strict):
    milestone_progress: Milestones = Field(default_factory=list)
    effort_person_days: Effort | None = None
    effort_breakdown: EffortBreakdown | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    requirement_progress: Requirements = Field(default_factory=list)
    risks_and_blockers: Risks = Field(default_factory=list)

    @model_validator(mode='after')
    def validated(self):
        _check_effort(self.effort_person_days, self.effort_breakdown)
        _check_size(self.model_dump(exclude_none=True))
        return self


class SubmitFields(DraftFields):
    milestone_progress: Milestones = Field(...)
    effort_person_days: Effort = Field(...)
    requirement_progress: Requirements = Field(...)
    risks_and_blockers: Risks = Field(...)


SECTIONS = (('milestone_progress', MilestoneEntry), ('requirement_progress', RequirementEntry),
            ('risks_and_blockers', RiskEntry))
StoredSection = Annotated[list[dict[str, JsonValue]], Field(max_length=100)]


def _check_legacy_json(value: JsonValue, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError('Report sections are nested too deeply')
    if isinstance(value, str):
        _plain(value)
    elif isinstance(value, list):
        for item in value:
            _check_legacy_json(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            _plain(key)
            _check_legacy_json(item, depth + 1)


class StoredFields(Strict):
    """Previously admissible bytes, for historical verification and exact-key replay only."""
    milestone_progress: StoredSection = Field(default_factory=list)
    effort_person_days: Effort | None = None
    effort_breakdown: EffortBreakdown | None = None
    requirement_progress: StoredSection = Field(default_factory=list)
    risks_and_blockers: StoredSection = Field(default_factory=list)

    @model_validator(mode='after')
    def validated(self):
        _check_effort(self.effort_person_days, self.effort_breakdown)
        value = self.model_dump(exclude_none=True)
        _check_legacy_json(value)
        _check_size(value)
        for section, model in SECTIONS:
            id_key = model.resource_key
            for item in getattr(self, section):
                if id_key in item:
                    uuid_value(item[id_key])
                if evidence_ids(item) and id_key not in item:
                    raise ValueError('Evidence references require a resource ID')
                if id_key == 'milestoneId' and 'claim' in item:
                    if item['claim'] not in ('progress', 'completion') or id_key not in item:
                        raise ValueError('A milestone claim needs its ID and a progress or completion kind')
        return self


class StoredSubmitFields(StoredFields):
    milestone_progress: StoredSection = Field(...)
    effort_person_days: Effort = Field(...)
    requirement_progress: StoredSection = Field(...)
    risks_and_blockers: StoredSection = Field(...)


def report_fields_view(fields: object) -> tuple[DraftFields, bool]:
    try:
        return DraftFields.model_validate(fields), False
    except ValidationError:
        original = StoredFields.model_validate(fields)
    payload = original.model_dump(exclude_none=True)
    for section, model in SECTIONS:
        payload[section] = [{key: value for key, value in entry.items() if key in model.model_fields
                             and (key not in ('summary', 'name', 'description') or isinstance(value, str))}
                            for entry in payload[section]]
    return DraftFields.model_validate(payload), True
