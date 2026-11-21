"""Run frozen inputs against product arithmetic and the live application, not the reference oracle."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.cost_math import cost_line
from pmo.health_calculation import aggregate
from pmo.reports import period_bounds, weekly_submissions
from pmo.review_scores import WEIGHTS, FeasibilityDimensions, assess
from test_health import close_for_lr08, code, prepare
from test_lifecycle import ANSWERS, begin_execution, execute, judgment, propose, stage
from test_reports import WEEK, body
from test_reviews import data, decide, headers, member, score, start_charter, team as team

VECTORS = json.loads((Path(__file__).parents[1] / 'docs/spec/04_PMO_V1_规则测试向量.json').read_text(encoding='utf-8'))


def cases(group, select=lambda case: True):
    return [pytest.param(case, id=f'{group}/{case["case"]}') for case in VECTORS[group] if select(case)]


def card(scores):
    if isinstance(scores, dict):
        values = dict(zip(WEIGHTS, (scores[key] for key in ('strategic_alignment', 'customer_attractiveness',
            'financial_resource_return', 'risk_controllability')), strict=True))
    else:
        values = dict(zip(FeasibilityDimensions.model_fields, scores, strict=True))
    return SimpleNamespace(dimensions={key: {'score': value} for key, value in values.items()})


@pytest.mark.parametrize('case', cases('scoring'))
def test_scoring(case):
    if 'expectedRecommendation' in case:
        rows = case['reviewers'] if 'reviewers' in case else [case['scores']]
        result = assess('charter', [card(scores) for scores in rows])
        classification = {'pass_with_remediation': 'normal', 'reject_or_redesign': 'below_threshold'}.get(
            case['expectedRecommendation'], case['expectedRecommendation'])
        assert (result.total, result.classification) == (case['expectedTotal'], classification)
    else:
        result = assess('feasibility', [card(case['scores'])])
        assert result.classification == {'reject': 'shortfall', 'pass_eligible': 'feasible'}[case['expected']]


@pytest.mark.parametrize('case', cases('cost', lambda c: 'budget' in c))
def test_cost_calculation(case):
    budget = None if case['budget'] is None else Decimal(case['budget'])
    result = cost_line(budget, Decimal(case['actual']), Decimal(case['forecastRemaining']),
                       zero_declared=bool(case.get('declaredZeroBudgetReason')))
    for key, value in {'expectedColor': result.color, 'expectedVariance': result.variance,
                       'lr04': result.overspend_alert, 'requireReestimate': result.reassess}.items():
        if key in case:
            expected = Decimal(case[key]) if key == 'expectedVariance' else case[key]
            assert value == expected, key
    if not budget:
        assert result.variance is None


@pytest.mark.parametrize('case', cases('cost', lambda c: 'postings' in c))
def test_cost_postings(case, live, team):
    pid, people, users, _ = team
    pm, finance, independent = (users[key] for key in ('pm', 'reviewer', 'sponsor'))
    path = f'projects/{pid}'
    with live[0].begin() as c:
        for person in (people['reviewer'], people['sponsor']):
            account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == person))
            c.execute(db.user_roles.insert().values(user_id=account, role='finance_verifier'))

    created = {}
    validation = case.get('expectedValidation')

    def listed(client, posting):
        return next(row for row in data(client.get(path + '/cost-postings'))['items']
                    if row['postingId'] == posting['postingId'])

    for index, row in enumerate(case['postings']):
        if 'postingOf' not in row:
            receipt = data(pm.post(path + '/cost-postings', headers=headers(), json={
                'type': 'money', 'amount': row['amount'], 'period': '2025-01', 'sourceRef': f'SYNTHETIC-{index}'}))
            if row['status'] == 'verified':
                revision = listed(finance, receipt)['revision']
                receipt = data(finance.post(f"cost-postings/{receipt['postingId']}/verify", headers=headers(revision),
                    json={'reason': 'Checked fixed synthetic original.', 'occurredAt': '2025-01-03T00:00:00Z'}))
            assert receipt['status'] == row['status']
            created[row.get('id', str(index))] = receipt
        else:
            parent = listed(finance, created[row['postingOf']])
            reverse = f"cost-postings/{parent['postingId']}/reverse"
            claim = {'reason': 'Correct the checked source amount.'}
            if validation == 'REJECT_PARTIAL_REVERSAL':
                code(finance.post(reverse, headers=headers(parent['revision']), json={**claim, 'amount': row['amount']}),
                     422, 'VALIDATION_FAILED')
                assert data(pm.get(path + '/costs'))['money']['actual'] == parent['amountSigned']
                assert data(finance.get(path + '/cost-postings'))['total'] == 1
                return
            response = finance.post(reverse, headers=headers(parent['revision']), json=claim)
            if validation == 'REJECT_DUPLICATE_REVERSAL' and index == 2:
                code(response, 409, 'REVERSAL_EXISTS')
                assert data(pm.get(path + '/costs'))['money']['actual'] == '0.00'
                assert data(finance.get(path + '/cost-postings'))['total'] == 2
                return
            receipt = data(response)
            assert Decimal(receipt['amountSigned']) == Decimal(row['amount'])
            if row['status'] == 'verified':
                revision = listed(independent, receipt)['revision']
                receipt = data(independent.post(f"cost-postings/{receipt['postingId']}/verify", headers=headers(revision), json=claim))
            assert receipt['status'] == row['status']
    assert validation is None
    assert data(pm.get(path + '/costs'))['money']['actual'] == case['expectedActual']


@pytest.mark.parametrize('case', cases('health', lambda c: 'lines' in c))
def test_health_composition(case):
    states = [*case['lines'], *([case['dataTrust']] if 'dataTrust' in case else [])]
    assert aggregate(states) == case['expected']


@pytest.mark.parametrize('case', cases('health', lambda c: 'p0UnconfirmedCount' in c))
def test_p0_live_health(case, live, team):
    pid, _, users, _ = team
    pm = users['pm']
    if case.get('stage') == 'drafting':
        result = data(pm.get(f'projects/{pid}/health'))
        assert (case['expectedCurrentHealth'], result['health']) == ('not_applicable', 'unavailable')
        assert result['gaps'] == ['HEALTH_NOT_ACTIVE'] and 'qualityLine' not in result
    else:
        path, _ = prepare(live, team, priority='P0')
        result = data(pm.get(path + '/health'))
        assert result['inputs']['quality']['metrics']['activeP0'] == case['p0UnconfirmedCount']
        assert result['inputs']['quality']['metrics']['confirmedP0'] == 0
        assert result['qualityLine'] == case['expectedQuality']


@pytest.mark.parametrize('case', cases('requirements', lambda c: c['case'].startswith('zero_')))
def test_no_requirement_denominator(case, live, team):
    del live  # team supplies the isolated HTTP stack.
    pid, _, users, _ = team
    metrics = data(users['pm'].get(f'projects/{pid}/requirement-metrics'))
    if 'expectedChangeRate' in case:
        assert case['expectedChangeRate'] == 'UNAVAILABLE' and metrics['initialCount'] == case['initialBaselineCount']
        assert metrics['approvedChangeCount'] == case['approvedChangedBaselineCount'] and metrics.get('changeRate') is None
    else:
        assert case['expected'] == 'UNAVAILABLE' and metrics['activeCount'] == len(case['items'])
        assert metrics.get('satisfaction') is None and 'NO_ACTIVE_REQUIREMENTS' in metrics['gaps']


@pytest.mark.parametrize('case', cases('report', lambda c: 'modelResult' not in c))
def test_report_submission_and_presence(case, live, team):
    pid, _, users, _ = team
    pm = users['pm']
    path = f'projects/{pid}/reports'
    if 'structuredFields' in case:
        response = pm.post(path, headers=headers(), json=body(structuredFields=case['structuredFields']))
        if case['expectedSubmission'] == 'ACCEPT':
            assert data(response)['status'] == 'submitted'
        else:
            code(response, 422, case['expectedSubmission'])
    else:
        target = path if case['status'] == 'submitted' else path + '/drafts'
        report = data(pm.post(target, headers=headers(), json=body()))
        assert report['status'] == case['status']
        start, end = period_bounds('weekly', WEEK)
        with live[0].connect() as c:
            receipts = weekly_submissions(c, pid, as_of=datetime.now(UTC), start=start, end=end)
        assert (not receipts) is case['expectedCountsAsMissing']


@pytest.mark.parametrize('case', cases('state'))
def test_state(case, live, team):
    pid, _, users, _ = team
    pm, owner, reviewer, sponsor = (users[key] for key in ('pm', 'initiator', 'reviewer', 'sponsor'))
    path = f'projects/{pid}'
    name = case['case']
    if name == 'skip_drafting_to_executing':
        response = propose(pm, path, case['toState'])
        code(response, 422, 'TRANSITION_NOT_ALLOWED')
        assert stage(pm, path)['stage'] == case['fromState']
    elif name == 'valid_charter_pass':
        review = start_charter(pid, users)
        assert stage(pm, path)['stage'] == case['fromState']
        assert decide(reviewer, review, verdict=case['decision']).status_code == 422
        score(reviewer, review, [4] * 4)
        assert data(decide(reviewer, review, verdict=case['decision']))['decision']['stage'] == case['toState']
    elif name == 'archive_without_lesson':
        close_for_lr08(live, team)
        data(execute(pm, path, data(propose(pm, path, case['fromState']))))
        retro = path + '/retrospective'
        data(pm.put(retro, headers=headers(data(pm.get(retro))['revision']), json={'answers': ANSWERS}))
        assert data(pm.get(path + '/lessons'))['total'] == case['confirmedLessons']
        code(propose(pm, path, case['toState']), 422, 'CONFIRMED_LESSON_REQUIRED')
    elif name == 'resume_hold':
        begin_execution(live, team)
        hold = data(propose(pm, path, case['fromState']))
        data(judgment(sponsor, hold, holdUntil=(datetime.now(UTC) + timedelta(hours=1)).isoformat()))
        resume = data(propose(pm, path, case['toState']))
        assert stage(pm, path)['stage'] == case['fromState']
        assert data(judgment(sponsor, resume))['toStage'] == case['toState']
    elif name in ('draft_without_sponsor_pm_can_save', 'draft_without_sponsor_pm_cannot_submit'):
        assert case['pmId'] is None and case['sponsorId'] is None
        with live[0].begin() as c:
            c.execute(db.projects.update().where(db.projects.c.id == pid).values(pm_id=None, sponsor_id=None))
        revision = stage(owner, path)['revision']
        if case.get('operation') == 'save_draft':
            updated = data(owner.patch(path, headers=headers(revision), json={'fields': {'name': 'Editable draft'}}))
            assert updated['stage'] == case['fromState'] and updated['revision'] == revision + 1
        else:
            gaps = data(owner.get(path + '/gates/check', params={'gate': 'charter'}))['missing']
            assert {'PM_ASSIGNMENT_REQUIRED', 'SPONSOR_ASSIGNMENT_REQUIRED'} <= {gap['code'] for gap in gaps}
            assert owner.post(path + '/reviews', headers=headers(revision), json={'reviewType': 'charter'}).status_code == 422
            assert stage(owner, path)['stage'] == case['fromState']
    elif name == 'review_decision_stale_hash':
        review = start_charter(pid, users)
        score(reviewer, review, [4] * 4)
        with live[0].begin() as c:
            c.execute(db.charters.update().where(db.charters.c.project_id == pid)
                .values(value_prop='Changed imported charter material', revision=db.charters.c.revision + 1))
        # The resource-specific code is the public REST spelling of the stale approval rejection.
        assert case['expected'] == 'APPROVAL_STALE'
        code(decide(reviewer, review), 409, 'REVIEW_SNAPSHOT_STALE')
        assert stage(pm, path)['stage'] == case['fromState']
    else:
        assert name == 'rejected_second_resubmit_forbidden' and case['resubmitsAlreadyUsed'] == 1
        for attempt in (1, 2):
            review = start_charter(pid, users)
            score(reviewer, review, [2] * 4)
            assert data(decide(reviewer, review, verdict='reject'))['decision']['stage'] == case['fromState']
            result = owner.post(path + '/resubmit', headers=headers(stage(owner, path)['revision']),
                                json={'reason': 'Revised the synthetic commercial assumptions.'})
            if attempt == 1:
                assert data(result)['toStage'] == case['toState']
            else:
                assert result.status_code == 422 and result.json()['code'] == 'CHARTER_RESUBMISSION_LIMIT'
