from decimal import Decimal as D

import pytest
from pmo.cost_math import cost_line


@pytest.mark.parametrize('remaining,color,alert,reassess', [
    ('35.00', 'green', False, False), ('35.01', 'yellow', False, False),
    ('45.00', 'yellow', False, False), ('45.01', 'red', True, False),
    ('50.00', 'red', True, False), ('50.01', 'red', True, True),
])
def test_thresholds(remaining, color, alert, reassess):
    line = cost_line(D('100.00'), D('70.00'), D(remaining))
    assert (line.color, line.overspend_alert, line.reassess) == (color, alert, reassess)
    assert line.variance == (line.projected - 100) / 100


def test_zero_and_missing_are_distinct():
    zero = D(0)
    assert cost_line(None, D(1), zero).color == 'untrusted'
    assert cost_line(zero, zero, zero).color == 'untrusted'
    assert cost_line(zero, zero, zero, zero_declared=True).color == 'green'
    line = cost_line(zero, zero, D('0.01'), zero_declared=True)
    assert line.color == 'red' and line.overspend_alert and line.variance is None


def test_display_rounding_does_not_hide_threshold_crossing():
    line = cost_line(D('5000000000000000'), D('5750000000000000.01'), D(0))
    assert line.overspend_alert and not line.reassess and line.color == 'red'
    assert format(line.variance, '.4f') == '0.1500'
