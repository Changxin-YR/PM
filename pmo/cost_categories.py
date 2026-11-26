"""The fixed posting and planning directory and safe summary projection."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from .contracts import Strict

Kind = Literal['money', 'effort']
Category = Literal['procurement', 'travel', 'cloud', 'allocation', 'development', 'implementation', 'management']
CATEGORY_KIND: dict[Category, Kind] = {
    'procurement': 'money', 'travel': 'money', 'cloud': 'money', 'allocation': 'money',
    'development': 'effort', 'implementation': 'effort', 'management': 'effort',
}

Amount = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$')]
DisplayedAmount = Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)\.[0-9]{2}$')]
DisplayedNet = Annotated[str, Field(pattern=r'^-?(0|[1-9][0-9]*)\.[0-9]{2}$')]
DisplayedRate = Annotated[str, Field(pattern=r'^-?(0|[1-9][0-9]*)\.[0-9]{4}$')]
CategoryAmounts = dict[Category, Amount]


class CategoryLine(Strict):
    type: Kind
    category: Category | Literal['historical_uncategorized']
    initialBudget: DisplayedAmount | None = None
    budget: DisplayedAmount | None = None
    actual: DisplayedNet
    forecastRemaining: DisplayedAmount | None = None
    projectedTotal: DisplayedNet | None = None
    varianceRate: DisplayedRate | None = None
    unbudgetedExposure: bool
    sourceRefs: list[str]


class CategorySummary(Strict):
    items: list[CategoryLine]
    mode: Literal['legacy', 'draft', 'classified']
    budgetRevision: int
    forecastRevision: int
    forecastObservedAt: datetime | None = None
    forecastEffectiveAt: datetime | None = None
    forecastNeedsReview: bool | None = None


class CategoryEditor(Strict):
    canSaveBudget: bool
    canSaveForecast: bool
    changeRevision: int | None = None
    draftAmounts: CategoryAmounts | None = None
    forecastAmounts: CategoryAmounts | None = None
