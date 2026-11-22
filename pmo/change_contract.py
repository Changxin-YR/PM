"""Closed change snapshot shapes shared by the API and the stdio adapter."""
from typing import Any, Literal

from jsonschema import Draft202012Validator

Target = Literal['requirement', 'milestone', 'budget', 'charter']
Schema = dict[str, Any]
DATE = {'type': 'string', 'format': 'date'}
PRIORITY = {'enum': ['P0', 'P1', 'P2']}
ACTIVE_STATUS = {'enum': ['baseline', 'delivered', 'accepted', 'customer_confirmed']}


def text(minimum: int, maximum: int) -> Schema:
    return {'type': 'string', 'pattern': r'\S', 'minLength': minimum, 'maxLength': maximum,
            'not': {'pattern': r'\u0000'}}


def amount(digits: int, *, stored: bool = False) -> Schema:
    fraction = r'\.[0-9]{2}' if stored else r'(\.[0-9]{1,2})?'
    return {'type': 'string', 'pattern': rf'^(0|[1-9][0-9]{{0,{digits - 1}}}){fraction}$'}


def patch(fields: Schema, *, complete: bool = False) -> Schema:
    return {'type': 'object', 'properties': fields, 'additionalProperties': False, 'minProperties': 1,
            **({'required': list(fields)} if complete else {})}


def nullable(field: Schema) -> Schema:
    return {'anyOf': [field, {'type': 'null'}]}


def variant(kind: Target, before: Schema, after: Schema, **kwargs: Any) -> Schema:
    return {'required': ['targetType', 'before', 'after'],
            'properties': {'targetType': {'const': kind}, 'before': before, 'after': after}, **kwargs}


def paired(kind: Target, before: Schema, after: Schema) -> Schema:
    """Both snapshots describe exactly the fields proposed for amendment."""
    return variant(kind, patch(before), patch(after), allOf=[
        {'if': {'properties': {'after': {'required': [key]}}},
         'then': {'properties': {'before': {'required': [key]}}},
         'else': {'properties': {'before': {'not': {'required': [key]}}}}}
        for key in after
    ])


REQUIREMENT = {'desc': text(5, 4000), 'acceptanceCriteria': text(5, 4000), 'priority': PRIORITY, 'plannedConfirmAt': DATE}
MILESTONE = {'name': text(1, 160), 'plannedDate': DATE, 'isKey': {'type': 'boolean'}}
CHARTER = {**dict.fromkeys(('valueProp', 'attractiveness', 'expectedBenefit', 'successCriteria'), text(1, 3000)),
           'targetClient': text(1, 2000), 'roughCost': amount(16), 'roughEffort': amount(10),
           'roughDuration': {'type': 'integer', 'minimum': 1, 'maximum': 3650},
           'topRisks': {'type': 'array', 'minItems': 1, 'maxItems': 100,
                        'items': patch({'statement': text(1, 3000), 'response': text(1, 3000)}, complete=True)}}
ADDITION = variant('requirement', patch({'present': {'const': False},
    'initialBaselineRevision': {'type': 'integer', 'minimum': 1},
    'initialBaselineHash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}, complete=True), patch(REQUIREMENT, complete=True))


REQUIREMENT_EDITS = [
    paired('requirement', {**REQUIREMENT, 'plannedConfirmAt': nullable(DATE)}, REQUIREMENT),
    variant('requirement', patch({'status': ACTIVE_STATUS}), patch({'status': {'const': 'cancelled'}})),
]


def snapshots(*, stored: bool) -> list[Schema]:
    return [*REQUIREMENT_EDITS, paired('milestone', MILESTONE, MILESTONE),
            variant('budget', patch({'budget': amount(16, stored=True)}), patch({'budget': amount(16, stored=stored)})),
            paired('charter', {k: nullable(v) for k, v in CHARTER.items()}, CHARTER)]


PROPOSAL: Schema = {'oneOf': snapshots(stored=False)}
RECEIPT_CASES = [*snapshots(stored=True), ADDITION]
HISTORY: Schema = {'oneOf': [
    *[{**case, 'not': {'required': ['legacySnapshot']}} for case in RECEIPT_CASES],
    {'description': 'Historical snapshot outside the current field contract; preserved for authorized human review.',
     'required': ['legacySnapshot'], 'properties': {'legacySnapshot': {'const': True}}},
]}
_FORMAT = Draft202012Validator.FORMAT_CHECKER
_proposed = Draft202012Validator(PROPOSAL, format_checker=_FORMAT)
_recorded = Draft202012Validator({'oneOf': RECEIPT_CASES}, format_checker=_FORMAT)


def valid_proposal(kind: Target, before: dict, after: dict) -> bool:
    return _proposed.is_valid({'targetType': kind, 'before': before, 'after': after})


def legacy_snapshot(kind: str, before: Any, after: Any) -> Literal[True] | None:
    return None if _recorded.is_valid({'targetType': kind, 'before': before, 'after': after}) else True


def mcp_snapshots(*, history: bool) -> Schema:
    if not history:
        return {'oneOf': snapshots(stored=True)}
    # Retain the row and source; arbitrary imported JSON remains human-only.
    return {'oneOf': [*REQUIREMENT_EDITS, ADDITION, {
        'properties': {'targetType': {'const': 'requirement'}},
        'not': {'anyOf': [{'required': ['before']}, {'required': ['after']}]},
    }]}
