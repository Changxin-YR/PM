"""The human health trace wire shape. Snapshot storage and hashing stay independent."""
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from .costs import DisplayedRate
from .dto import Strict, Unavailable
from .reports import ClaimKind
from .scope_quality import Quality


def _day(value: str) -> str:
    date.fromisoformat(value)
    return value


def _instant(value: str) -> str:
    if datetime.fromisoformat(value).utcoffset() is None:
        raise ValueError('The timestamp requires a timezone')
    return value


Color = Literal['green', 'yellow', 'red']
DayText = Annotated[str, Field(json_schema_extra={'format': 'date'}), AfterValidator(_day)]
TimeText = Annotated[str, Field(json_schema_extra={'format': 'date-time'}), AfterValidator(_instant)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
# Early snapshots use "0" for empty sums. Do not reformat archived decimal text.
TraceDecimal = Annotated[str, Field(pattern=r'^-?(0|[1-9][0-9]*)(.[0-9]{1,2})?$')]


class EvidenceTrace(Strict):
    id: str = Field(json_schema_extra={'format': 'uuid'})
    revision: int
    sha256: Digest
    verifiedAt: TimeText


class CompletionTrace(Strict):
    receiptRevision: int
    recordedAt: TimeText
    proof: EvidenceTrace


class MilestoneTrace(Strict):
    source: str
    name: str
    key: bool
    plannedDate: DayText
    forecastDate: DayText | None
    forecastMissing: bool
    delayDays: int
    actualDate: DayText | None
    completion: CompletionTrace | None
    actualDelayDays: int | None
    color: Color | None


class CostTrace(Strict):
    budget: TraceDecimal | None
    actual: TraceDecimal
    remaining: TraceDecimal
    projected: TraceDecimal
    color: Literal[Color, 'untrusted']
    variance: DisplayedRate | Unavailable
    overspend: bool
    reassess: bool
    sourceRefs: list[str]
    budgetRefs: list[str]


class CostTraces(Strict):
    money: CostTrace
    effort: CostTrace


class RequirementTrace(Strict):
    requirement_id: str = Field(json_schema_extra={'format': 'uuid'})
    revision: int | None
    priority: Literal['P0', 'P1', 'P2']
    planned: DayText | None
    confirmed: bool
    frozen: bool
    disputed: bool | None = None  # Not present in pre-dispute snapshots.
    lagWeeks: int
    color: Color


class QualityTrace(Strict):
    metrics: Quality
    schedule: list[RequirementTrace]


class ReportTrace(Strict):
    source: str
    hash: Digest
    period: str
    submittedAt: TimeText
    confirmedAt: TimeText | None
    confirmedRevision: int | None


class ClaimTrace(Strict):
    report: str
    period: str
    milestone: str
    kind: ClaimKind
    item: int
    evidence: list[EvidenceTrace]


class WeeklyTrace(Strict):
    due: list[str]
    missingPeriods: list[str]
    twoMissed: bool
    confirmed: bool
    receipts: list[ReportTrace]
    claims: list[ClaimTrace]
    missingEvidence: list[ClaimTrace]


class HealthTrace(Strict):
    day: DayText
    stage: Literal['executing', 'on_hold', 'delivering', 'closing_review']
    time: list[MilestoneTrace]
    cost: CostTraces
    quality: QualityTrace
    weekly: WeeklyTrace
