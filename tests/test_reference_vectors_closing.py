"""Isolate each closing condition while keeping the other signed sources valid."""
from decimal import Decimal

import pytest

from test_health import code
from test_lifecycle import gate, stage
from test_reference_vectors_governance import lr
from test_reports import body
from test_requirement_exceptions import deliver_with_exception_material
from test_reviews import data, decide, headers, member, result_counts, team as team


def assert_closing(live, team, project, expected_gaps, *, conditional=False):
    pid, _, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    checked = data(pm.get(project + '/closing/check'))
    assert {gap['code'] for gap in checked['missing']} == expected_gaps
    assert checked['eligible'] is (not expected_gaps)
    if expected_gaps:
        with live[0].connect() as c:
            before = result_counts(c, pid)
        code(pm.post(project + '/reviews', headers=headers(stage(pm, project)['revision']), json={'reviewType': 'closing'}),
             422, 'GATE_REQUIREMENTS_NOT_MET')
        with live[0].connect() as c:
            assert result_counts(c, pid) == before
        assert stage(pm, project)['stage'] == 'delivering'
    else:
        review = gate(pm, project, 'closing')
        result = data(decide(reviewer, review, verdict='conditional_pass' if conditional else 'pass'))
        assert result['decision']['stage'] == 'closed'
        assert ('requirementException' in result) is conditional


@pytest.mark.parametrize('case', lr('LR-07'))
def test_independent_closing_conditions(case, live, team):
    pid, people, users, _ = team
    pm, customer, sponsor = (users[i] for i in ('pm', 'client_representative', 'sponsor'))
    satisfaction = Decimal(case['requirementSatisfaction'])
    # One outstanding P0 (weight 3); the other requirements are independently accepted.
    weights = {Decimal('1.00'): (), Decimal('0.85'): ('P0',) * 5 + ('P1',), Decimal('0.80'): ('P0',) * 4}
    variances = [Decimal(case[f'{kind}AbsVariance']) for kind in ('money', 'effort')]
    actuals = tuple(f'{budget * (1 + variance):.2f}' for budget, variance in zip((100, 10), variances, strict=True))
    project, acceptance = deliver_with_exception_material(live, team, close_risk=case['blockingRisksResolved'],
        confirm=case['p0Confirmed'], extra_priorities=weights[satisfaction], actuals=actuals)
    measured = data(pm.get(project + '/requirement-metrics'))
    assert (measured['confirmedP0'] == measured['activeP0']) is case['p0Confirmed']
    assert Decimal(measured['satisfaction']) == satisfaction
    if case.get('sponsorReasonProvided'):
        with live[0].begin() as c:
            member(c, pid, people['sponsor'], people['initiator'], 'member')  # The explanation is a human report, not an approval.
        report = data(sponsor.post(project + '/reports', headers=headers(), json=body(
            narrative='Sponsor acknowledges the estimate overrun and requests closure without a budget revision.')))
        assert (report['status'], report['authorId']) == ('submitted', str(people['sponsor']))
    valid_exception = case.get('validRequirementException', False)
    if case['signedException']:
        state, command = acceptance(verify=valid_exception)
        response = customer.post(project + '/requirement-exceptions', headers=headers(state['revision']), json=command)
        if valid_exception:
            assert data(response)['items'][0]['requirementId'] == state['gaps'][0]['requirementId']
        else:
            code(response, 422, 'REQUIREMENT_EXCEPTION_EVIDENCE_REQUIRED')
    expected_gaps = set()
    if not case['p0Confirmed'] and not valid_exception:
        expected_gaps.add('CUSTOMER_ACCEPTANCE_REQUIRED')
    if not case['blockingRisksResolved']:
        expected_gaps.add('BLOCKING_RISK_UNRESOLVED')
    for kind, variance in zip(('MONEY', 'EFFORT'), variances, strict=True):
        if variance > Decimal('0.20'):
            expected_gaps.add(kind + '_CLOSING_VARIANCE')
    assert bool(expected_gaps) is case['expectedTriggered']
    assert_closing(live, team, project, expected_gaps, conditional=valid_exception)
