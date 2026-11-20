"""Score DTOs, original anchors and deterministic gate arithmetic."""
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import Strict

Anchor = Literal['score_1', 'between_1_3', 'score_3', 'between_3_5', 'score_5']
ANCHORS = ('score_1', 'between_1_3', 'score_3', 'between_3_5', 'score_5')
Reason = Annotated[str, Field(min_length=4, max_length=1500, pattern=r'\S')]
Source = Annotated[str, Field(min_length=4, max_length=200)]


class DimensionScore(Strict):
    score: int = Field(strict=True, ge=1, le=5)
    selectedAnchorCode: Anchor
    rationale: Reason
    evidenceRefs: list[Source] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def justified(self):
        if self.selectedAnchorCode != ANCHORS[self.score - 1] or len(self.rationale.strip()) < 4:
            raise ValueError('Score, anchor and rationale must match')
        if len(set(self.evidenceRefs)) != len(self.evidenceRefs):
            raise ValueError('Duplicate source references')
        return self


class CharterDimensions(Strict):
    strategicAlignment: DimensionScore
    customerAttractiveness: DimensionScore
    financialResourceReturn: DimensionScore
    riskControllability: DimensionScore


class FeasibilityDimensions(Strict):
    technicalPath: DimensionScore
    resourceMatch: DimensionScore
    externalDependencies: DimensionScore
    timeReasonableness: DimensionScore
    acceptanceCriteria: DimensionScore


class CharterScore(Strict):
    reviewType: Literal['charter']
    dimensions: CharterDimensions
    round: int = Field(strict=True, ge=1, le=2)


class FeasibilityScore(Strict):
    reviewType: Literal['feasibility']
    dimensions: FeasibilityDimensions
    round: int = Field(strict=True, ge=1, le=2)


Score = Annotated[CharterScore | FeasibilityScore, Field(discriminator='reviewType')]
WEIGHTS = dict(zip(CharterDimensions.model_fields, (20, 30, 25, 25)))


class Dimension(Strict):
    key: str
    label: str
    anchors: dict[Anchor, str]


# Original §3.1 descriptions (1/3/5); §3.2 defines examples of a shortfall only.
CHARTER_TEXT = (
    ('战略对齐', '与任何主线无明确关联', '与某条主线相关，但贡献方式牵强', '直接支撑明确主线，缺了会有缺口'),
    ('客户/市场吸引力', '客户是谁说不清；纯内部设想', '有明确客户与真实需求，但付费意愿未验证', '客户明确 + 付费/立项意愿已验证 + 有示范或复制效应'),
    ('财务/资源回报', '纯投入无回报路径，且人天占用大', '回报可期但周期长或不确定', '回报明确可量化（合同额 / 降本 / 强制性能力），投入产出清晰'),
    ('风险可控度', '关键依赖全部在外部且无承诺', '有识别主要风险，应对思路粗略', '风险清单完整，每条有应对与触发条件，无单点致命项'),
)
FEASIBILITY_TEXT = (
    ('技术路径', '核心环节「再研究」，无备选路径'),
    ('资源匹配', '计划依赖「还没招到的人」或关键人 100% 双投'),
    ('外部依赖', '依赖客户 / 第三方的环节「对方应该没问题」'),
    ('时间合理性', '首尾里程碑挤压全部工作量；零缓冲'),
    ('验收标准', '验收标准写「按客户满意度」'),
)


def rubric(gate):
    if gate == 'charter':
        return [Dimension(key=k, label=label, anchors=dict(zip(ANCHORS, (one, '高于 1 分、未达到 3 分；说明证据。',
            three, '高于 3 分、未达到 5 分；说明证据。', five)))) for k, (label, one, three, five) in zip(WEIGHTS, CHARTER_TEXT)]
    if gate == 'feasibility':
        return [Dimension(key=k, label=label, anchors=dict(zip(ANCHORS, (f'短板（原文示例：{shortfall}）',
            f'短板（原文示例：{shortfall}）；说明为何给 2 分。', '非短板（3 分）；以证据说明。',
            '介于 3 和 5 分；以证据说明。', '5 分；以证据说明。')))) for k, (label, shortfall) in
            zip(FeasibilityDimensions.model_fields, FEASIBILITY_TEXT)]
    return []


Classification = Literal['veto', 'below_threshold', 'normal', 'fast_track', 'shortfall', 'feasible']


class Assessment(Strict):
    classification: Classification
    total: str | None = None
    means: dict[str, str]
    weakDimensions: list[str]


def assess(gate, cards):
    if not cards or gate == 'closing':
        return None
    charter = gate == 'charter'
    keys = WEIGHTS if charter else FeasibilityDimensions.model_fields
    minimums = {k: min(card.dimensions[k]['score'] for card in cards) for k in keys}
    sums = {k: sum(card.dimensions[k]['score'] for card in cards) for k in keys}
    n = len(cards)
    # Compare exact integers over a shared denominator; decimal formatting is display only.
    total = None
    classification: Classification
    with localcontext() as ctx:
        ctx.prec = 50
        if charter:
            weighted = sum(sums[k] * weight for k, weight in WEIGHTS.items())
            total = format(Decimal(weighted) / (100 * n), '.2f')
            classification = ('veto' if 1 in minimums.values() else 'fast_track' if weighted >= 420*n else
                              'normal' if weighted >= 320*n else 'below_threshold')
            weak = [k for k in keys if sums[k] <= 3*n]
        else:
            weak = [k for k in keys if minimums[k] <= 2]
            classification = 'shortfall' if weak else 'feasible'
        return Assessment(classification=classification, total=total,
            means={k: format(Decimal(sums[k]) / n, '.2f') for k in keys}, weakDimensions=weak)
