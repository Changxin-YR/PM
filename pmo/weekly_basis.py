"""Closed, read-only DTO for a generated weekly report's source receipt."""
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, StrictStr

from .contracts import Strict

Color = Literal['green', 'yellow', 'red', 'untrusted']
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class WeeklyHealth(Strict):
    health: Color | Literal['unavailable']
    dataTrust: Literal['trusted', 'untrusted', 'unavailable']
    timeLine: Color | None = None
    costLine: Color | None = None
    qualityLine: Color | None = None
    requirementSatisfactionRate: str | None = None
    gaps: list[str]


class WeeklyFact(Strict):
    sourceRef: str
    reportRef: str
    reportHash: Digest
    quotePath: str
    quoteOffset: int
    quote: str
    subject: str
    predicate: str
    value: StrictStr | StrictInt | StrictBool
    trust: Literal['high', 'medium']
    asOf: AwareDatetime
    evidenceRef: str | None = None
    evidenceHash: Digest | None = None


class WeeklyBasis(Strict):
    asOf: AwareDatetime
    ruleVersion: str
    projectRevision: int
    sourceHash: Digest
    sourceRefs: list[str]
    verifiedPeriodPersonDays: str
    verifiedPeriodPostingCount: int
    verifiedPeriodPostingRefs: list[str]
    health: WeeklyHealth
    verifiedFacts: list[WeeklyFact] = Field(max_length=20)
    omittedVerifiedFacts: int
    omittedRequirements: int
    omittedRisks: int
