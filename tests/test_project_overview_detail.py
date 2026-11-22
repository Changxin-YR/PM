from datetime import datetime

from jsonschema import Draft202012Validator
import sqlalchemy as sa

from pmo.health_calculation import TZ
from test_health import code, prepare, sample
from test_lifecycle import propose
from test_reference_vectors_permissions import bearer, denied_audit
from test_reports import body, sections
from test_reviews import data, headers, team as team


def test_overview_detail_returns_current_metrics_and_committed_edges(live, team, actor):
    engine, api, _, _ = live
    pid, _, users, _ = team
    pm, applicant, outsider = (users[key] for key in ('pm', 'initiator', 'outsider'))
    project, _ = prepare(live, team)
    overview = f'project-overview/{pid}'
    first = sample(pm, project)
    full_project = data(pm.get(project))
    response = pm.get(overview)
    detail = data(response)
    assert detail['project'] == full_project['project'] and detail['materialsVisible']
    assert detail['charterSummary'] == full_project['charterSummary']
    assert detail['indicators'] == {key: first[key] for key in ('health', 'publication', 'asOf', 'ruleVersion',
        'snapshotId', 'timeLine', 'moneyVarianceRate', 'effortVarianceRate', 'requirementSatisfactionRate')}
    assert detail['indicators']['moneyVarianceRate'] == '0.0000'
    transitions = detail['recentTransitions']
    assert [edge['toStage'] for edge in transitions] == ['executing', 'feasibility_review', 'designing', 'charter_review']
    assert all(edge['toRevision'] == edge['fromRevision'] + 1 for edge in transitions)
    newest = transitions[0]
    with engine.connect() as c:
        receipt = c.execute(sa.text('''SELECT request_id, review_id FROM project_stage_transitions
            WHERE project_id=:pid AND to_revision=:rev'''), {'pid': pid, 'rev': newest['toRevision']}).one()
    assert newest['requestId'] == str(receipt.request_id) and newest['reviewId'] == str(receipt.review_id)
    assert f"project-transition:{pid}@{newest['toRevision']}" in response.json()['meta']['sourceRefs']
    assert f'review:{receipt.review_id}' in response.json()['meta']['sourceRefs']
    assert f"health-snapshot:{first['snapshotId']}" in response.json()['meta']['sourceRefs']

    schema = api.get('/openapi.json').raise_for_status().json()
    route = schema['paths']['/api/v1/project-overview/{projectId}']['get']
    assert route['x-actorTypes'] == ['human'] and route['x-forbidAgentEvenViaHttp']
    model = route['responses']['200']['content']['application/json']['schema']
    Draft202012Validator({**model, 'components': schema['components']}).validate(response.json())

    code(outsider.get(overview), 404, 'RESOURCE_NOT_FOUND')
    restricted = data(applicant.get(overview))
    assert restricted == {'project': full_project['project'], 'materialsVisible': False, 'recentTransitions': []}
    agent, _ = actor(extra_actions=('human_project_overview_get',))
    blocked = api.get('/api/v1/' + overview, headers=bearer(agent))
    code(blocked, 403, 'FORBIDDEN')
    denied_audit(engine, blocked, 'human_project_overview_get')

    pending = data(propose(pm, project, 'on_hold'))
    assert pending['status'] == 'pending'
    assert data(pm.get(overview))['recentTransitions'] == transitions

    milestone = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    data(pm.post(project + '/reports', headers=headers(), json=body(reportType='daily', period=datetime.now(TZ).date().isoformat(),
        structuredFields=sections(milestone_progress=[{'milestoneId': milestone, 'claim': 'progress'}]))))
    after = data(pm.get(overview))
    assert after['indicators'] == {'health': 'untrusted', 'publication': 'withheld',
        'asOf': after['indicators']['asOf'], 'ruleVersion': first['ruleVersion']}
    assert datetime.fromisoformat(after['indicators']['asOf']) > datetime.fromisoformat(first['asOf'])
    assert after['recentTransitions'] == transitions
    assert data(pm.get(project + '/health/history'))['total'] == 1


def test_overview_detail_draft_has_no_fabricated_health_or_activity(live, team):
    pid, _, users, _ = team
    detail = data(users['initiator'].get(f'project-overview/{pid}'))
    assert detail['project']['stage'] == 'drafting'
    assert detail['recentTransitions'] == [] and 'charterSummary' not in detail
    assert detail['indicators']['health'] == detail['indicators']['publication'] == 'unavailable'
    assert 'timeLine' not in detail['indicators'] and 'moneyVarianceRate' not in detail['indicators']
