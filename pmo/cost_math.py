"""Authoritative thresholds use exact NUMERIC amounts, not formatted ratios."""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal


@dataclass(frozen=True)
class CostLine:
    projected: Decimal
    variance: Decimal | None
    color: Literal['untrusted', 'green', 'yellow', 'red']
    overspend_alert: bool
    reassess: bool


def cost_line(budget: Decimal | None, actual: Decimal, remaining: Decimal, *, zero_declared: bool = False) -> CostLine:
    with localcontext() as ctx:
        # NUMERIC(18,2) inputs may be summed across a project.
        ctx.prec = 64
        projected = actual + remaining
        if budget is None:
            return CostLine(projected, None, 'untrusted', False, False)
        if budget < 0 or actual < 0 or remaining < 0:
            raise ValueError('Negative budget, net actual or remaining forecast')
        if not budget:
            if projected > 0:
                return CostLine(projected, None, 'red', True, False)
            return CostLine(projected, None, 'green' if zero_declared else 'untrusted', False, False)
        excess = projected - budget
        alert = excess * 100 > budget * 15
        reassess = excess * 100 > budget * 20
        color: Literal['green', 'yellow', 'red'] = 'red' if alert else 'yellow' if excess * 100 > budget * 5 else 'green'
        return CostLine(projected, excess / budget, color, alert, reassess)
