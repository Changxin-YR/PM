"""Score DTOs, original anchors and deterministic gate arithmetic."""
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from .contracts import Strict

Anchor = Literal['score_1', 'between_1_3', 'score_3', 'between_3_5', 'score_5']
ANCHORS = ('score_1', 'between_1_3', 'score_3', 'between_3_5', 'score_5')
Reason = Annotated[str, Field(min_length=4, max_length=1500, pattern=r'\S')]
Source = Annotated[str, Field(min_length=4, max_length=200)]
ANCHOR_VERSION = 'pmo-lite-r1-dec20-20261124-v1'


class Judgment(Strict):
    criterion: str = Field(min_length=1, max_length=80)
    value: Literal['yes', 'no', 'unknown']
    evidenceRef: Source | None = None

    @model_validator(mode='after')
    def needs_source(self):
        if self.value != 'unknown' and self.evidenceRef is None:
            raise ValueError('A definite judgment requires a versioned source')
        return self


class Criterion(Strict):
    key: str
    label: str
    minimumScore: int


class Warning(Strict):
    criterion: str
    label: str
    kind: Literal['conflict', 'insufficient']
    evidenceRef: Source | None = None


class DimensionScore(Strict):
    model_config = ConfigDict(json_schema_extra={'allOf': [
        {'if': {'properties': {'score': {'const': score}}, 'required': ['score']},
         'then': {'properties': {'selectedAnchorCode': {'const': anchor}}}}
        for score, anchor in enumerate(ANCHORS, 1)
    ]})
    score: int = Field(strict=True, ge=1, le=5)
    selectedAnchorCode: Anchor
    rationale: Reason
    evidenceRefs: list[Source] = Field(min_length=1, max_length=12, json_schema_extra={'uniqueItems': True})
    judgments: list[Judgment] | None = Field(default=None, max_length=8)
    retainedReason: Reason | None = None

    @model_validator(mode='after')
    def justified(self):
        if self.selectedAnchorCode != ANCHORS[self.score - 1] or len(self.rationale.strip()) < 4:
            raise ValueError('Score, anchor and rationale must match')
        if len(set(self.evidenceRefs)) != len(self.evidenceRefs):
            raise ValueError('Duplicate source references')
        if self.retainedReason is not None and len(self.retainedReason.strip()) < 4:
            raise ValueError('Explain why the selected score is retained')
        judgments = self.judgments or []
        if len({item.criterion for item in judgments}) != len(judgments):
            raise ValueError('Duplicate structured judgments')
        if any(item.evidenceRef is not None and item.evidenceRef not in self.evidenceRefs for item in judgments):
            raise ValueError('Judgment sources must be cited by the dimension')
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
    criteria: list[Criterion] | None = None
    anchorVersion: str | None = None


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

CHARTER_INTERMEDIATE = (
    ('能指出拟关联主线，但项目成果对主线的贡献路径及依据不足。',
     '主线、成果及直接贡献路径有据可查，尚不能证明项目不可替代或缺少会产生明确缺口。'),
    ('已识别目标客户或群体，真实需求尚未由有效客户证据确认。',
     '客户及真实需求清楚，付费或立项意愿已验证，示范或复制效应的证据不足。'),
    ('有回报假设，但缺少可核查的关键数量、归因或投入估计，难以判断投入产出。',
     '回报、投入和关键假设可量化且有依据，仍有一项重要兑现条件待验证。'),
    ('已识别部分主要风险，但关键风险缺负责人、应对或触发条件。',
     '主要风险都有负责人、应对及触发条件，少数剩余依赖或应对有效性待验证。'),
)
FEASIBILITY_INTERMEDIATE = (
    ('核心路径仍需研究，缺验证证据，也没有可实施的备选。',
     '核心路径已有可重复验证，关键假设和备选明确，仅余有负责人和期限的非关键优化。'),
    ('关键交付依赖未到岗人员或超出关键人员可用容量，且无已落实替代。',
     '角色、技能、可用时段和容量已有确认，计划在实际容量内，并有替补或应急安排。'),
    ('关键外部依赖仅有口头预期，缺书面承诺、范围或交付条件。',
     '各关键依赖均有可核验书面承诺，责任方、范围、日期和验收条件明确，剩余核对有应对安排。'),
    ('关键链路被压缩，必要环节缺工期或缓冲，现有资源无法支持承诺日期。',
     '工作分解、依赖、资源日历和必要缓冲均有依据，非关键优化不影响当前可执行性。'),
    ('关键验收缺对象、指标、方法或有效确认依据，仍依赖主观满意度。',
     '关键需求可逐项验收，对象、指标、方法、证据和确认人均明确并有有效依据。'),
)
CRITERIA = {
    'strategicAlignment': (('directContribution', '有证据支持项目成果直接贡献于明确主线', 4),
                           ('criticalContribution', '缺少该成果会形成明确缺口', 5)),
    'customerAttractiveness': (('customerKnown', '有明确的客户或客户群体', 3),
        ('demandVerified', '客户真实需求已经核验', 3),
        ('willingnessVerified', '付费或立项意愿已经核验', 4),
        ('replicableValue', '有示范或复制效应证据', 5)),
    'financialResourceReturn': (('quantifiedReturn', '回报与投入可量化且有依据', 4),
                                 ('returnConditionsVerified', '关键兑现条件已有依据', 5)),
    'riskControllability': (('riskResponses', '主要风险已有负责人、应对和触发条件', 4),
                            ('noCriticalDependency', '风险清单完整且无致命单点', 5)),
    'technicalPath': (('verifiedPath', '核心路径有可重复验证且备选明确', 4),),
    'resourceMatch': (('confirmedCapacity', '人员、时段和容量已有有效确认', 4),),
    'externalDependencies': (('writtenCommitments', '关键依赖的范围、日期及验收条件有书面承诺', 4),),
    'timeReasonableness': (('feasibleSchedule', '关键依赖、工期、容量和缓冲有依据', 4),),
    'acceptanceCriteria': (('verifiableAcceptance', '关键需求的对象、指标、方法及确认人明确', 4),),
}


def criteria(key):
    return [Criterion(key=code, label=label, minimumScore=minimum) for code, label, minimum in CRITERIA[key]]


def rubric(gate):
    if gate == 'charter':
        return [Dimension(key=k, label=label, anchors=dict(zip(ANCHORS, (one, two, three, four, five))),
            criteria=criteria(k), anchorVersion=ANCHOR_VERSION) for k, (label, one, three, five), (two, four) in
            zip(WEIGHTS, CHARTER_TEXT, CHARTER_INTERMEDIATE, strict=True)]
    if gate == 'feasibility':
        return [Dimension(key=k, label=label, anchors=dict(zip(ANCHORS, (f'短板（原文示例：{shortfall}）',
            two, '非短板（3 分）；以证据说明。', four, '5 分；以证据说明。'))),
            criteria=criteria(k), anchorVersion=ANCHOR_VERSION) for k, (label, shortfall), (two, four) in
            zip(FeasibilityDimensions.model_fields, FEASIBILITY_TEXT, FEASIBILITY_INTERMEDIATE, strict=True)]
    return []


def judgment_warnings(key, rating):
    supported = {criterion.key: criterion for criterion in criteria(key) if criterion.minimumScore <= rating['score']}
    submitted = {item['criterion']: item for item in rating.get('judgments', [])}
    if submitted.keys() - supported.keys():
        raise ValueError('Unknown criterion for selected anchor')
    warnings = []
    for code, criterion in supported.items():
        item = submitted.get(code, {})
        if item.get('value') != 'yes':
            warnings.append(Warning(criterion=code, label=criterion.label,
                kind='conflict' if item.get('value') == 'no' else 'insufficient', evidenceRef=item.get('evidenceRef')))
    return warnings


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
