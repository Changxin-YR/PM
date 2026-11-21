"""The frozen timeout case uses a delayed loopback gateway; no external model or credentials."""
from time import sleep

import pytest
import sqlalchemy as sa

from pmo import model_gateway, report_analysis_worker as worker, schema_v1 as db
from test_facts import report_path, submit
from test_reference_vectors import cases
from test_report_analysis import NARRATIVE, analysis_api as analysis_api, answer, gateway, history, request, session_at
from test_reports import data, team as team


@pytest.mark.parametrize('case', cases('report', lambda c: 'modelResult' in c))
def test_model_failure_keeps_manual_report(case, team, analysis_api, monkeypatch):
    assert case['modelResult'] == 'timeout'
    author = team.users['reporter']
    report = submit(author, team.path, narrative=NARRATIVE)

    def slow(_):
        sleep(.8)
        return answer()

    with session_at(author, analysis_api) as user, gateway(slow) as (url, observed):
        pending = data(request(user, report))
        monkeypatch.setenv('PMO_REPORT_ANALYSIS', '1')
        monkeypatch.setenv('PMO_REPORT_MODEL_URL', url)
        monkeypatch.setattr(model_gateway, 'DEADLINE_SECONDS', .4)
        assert worker.dispatch(team.engine, 5) == 1
        result, = history(user, report)['items']
        assert (result['runId'], result['status'], result['errorCode']) == (pending['runId'], 'manual', 'MODEL_TIMEOUT')
        assert len(observed) == 1 and result['fallbackTaskId'] and not result['candidates']
    current = data(author.get(report_path(report)))
    assert (current == report) is case['expectedReportPreserved']
    with team.engine.connect() as c:
        facts = c.scalars(sa.select(db.facts.c.id).where(db.facts.c.report_id == report['reportId'])).all()
    assert bool(facts) is case['expectedFactAutoVerified']
