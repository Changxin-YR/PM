from datetime import datetime, timedelta

from jsonschema import Draft202012Validator
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.health_calculation import TZ
from test_health import code, prepare, sample
from test_reference_vectors_permissions import bearer, denied_audit
from test_reports import body, sections
from test_reviews import data, headers, issue, team as team


PATH = 'project-overview'
OP = 'human_project_overview_list'


def test_overview_pages_with_authorized_count_and_draft_capability(live, team, actor):
    engine, api, _, _ = live
    pid, people, users, _ = team
    initiator, pm, outsider = (users[role] for role in ('initiator', 'pm', 'outsider'))
    path = f'projects/{pid}'
    other = data(outsider.post('projects', headers=headers(), json={'code': 'OVERVIEW-OTHER', 'name': 'Synthetic review sibling'}))
    own = data(pm.post('projects', headers=headers(), json={'code': 'OVERVIEW-PM', 'name': 'Synthetic review local'}))
    foreign_token, _ = actor()
    foreign = data(api.post('/api/v1/projects', headers={**headers(), **bearer(foreign_token)},
                            json={'code': 'OVERVIEW-FOREIGN', 'name': 'Synthetic review foreign'}))

    params = {'filters[stage]': 'drafting', 'filters[keyword]': 'synthetic review', 'pageSize': 1}
    response = pm.get(PATH, params=params)
    first = data(response)
    second_response = pm.get(PATH, params={**params, 'page': '2'})
    second = data(second_response)
    local, shared = first['items'][0], second['items'][0]
    assert first['total'] == second['total'] == 2
    assert local['projectId'] == own['projectId'] and local['canEditDraft'] and 'manager' not in local
    assert shared['projectId'] == pid and shared['materialsVisible'] and not shared['canEditDraft']
    assert shared['manager'] == {'personId': str(people['pm']), 'name': 'pm'}
    assert 'healthSample' not in shared and local['projectId'] not in second_response.text
    assert other['projectId'] not in response.text and foreign['projectId'] not in response.text
    assert data(pm.get(PATH, params={**params, 'page': str(10**21)}))['items'] == []
    assert data(pm.get(PATH, params={**params, 'filters[stage]': 'designing'}))['total'] == 0
    assert data(pm.get(PATH, params={'filters[keyword]': '%'}))['total'] == 0
    assert data(outsider.get(PATH, params=params))['total'] == 1
    assert data(initiator.get(PATH, params=params))['items'][0]['canEditDraft']
    with engine.begin() as c:
        account = c.scalar(sa.select(db.persons.c.user_id).where(db.persons.c.id == people['outsider']))
        c.execute(db.user_roles.insert().values(user_id=account, role='system_admin'))
    assert [row['projectId'] for row in data(outsider.get(PATH))['items']] == [other['projectId']]
    code(pm.patch(path, headers=headers(), json={'fields': {'name': 'Unauthorized edit'}}), 403, 'FORBIDDEN')
    code(pm.get(PATH, params={'filters[orgId]': foreign['orgId']}), 422, 'VALIDATION_FAILED')
    code(api.get('/api/v1/' + PATH), 401, 'UNAUTHORIZED')

    spec = api.get('/openapi.json').raise_for_status().json()
    route = spec['paths']['/api/v1/' + PATH]['get']
    assert route['x-actorTypes'] == ['human'] and route['x-forbidAgentEvenViaHttp'] is True
    schema = route['responses']['200']['content']['application/json']['schema']
    Draft202012Validator({**schema, 'components': spec['components']}).validate(second_response.json())

    agent_token, _ = actor(extra_actions=(OP,))
    refused = api.get('/api/v1/' + PATH, headers=bearer(agent_token))
    code(refused, 403, 'FORBIDDEN')
    denied_audit(engine, refused, OP)
    agent_home = issue(pm, pid, ['pmo_project_list'])
    agent_item, = data(api.get('/api/v1/projects', headers=bearer(agent_home)))['items']
    assert agent_item == {k: v for k, v in shared.items() if k not in {'manager', 'materialsVisible', 'canEditDraft'}}

    # A stale PM field without an accepted appointment is not a responsible PM.
    with engine.begin() as c:
        c.execute(db.project_members.update().where(db.project_members.c.project_id == pid,
            db.project_members.c.person_id == people['pm']).values(revoked_at=sa.func.now()))
    assert 'manager' not in data(initiator.get(PATH, params=params))['items'][0]
    assert data(pm.get(PATH, params=params))['total'] == 1


def test_overview_uses_only_dated_health_samples_without_recalculating(live, team):
    engine, _, _, _ = live
    pid, _, users, _ = team
    pm, initiator = users['pm'], users['initiator']
    project, _ = prepare(live, team)

    def summary(client=pm):
        response = client.get(PATH, params={'filters[stage]': 'executing'})
        item, = data(response)['items']
        return response.json()['meta'], item

    assert 'healthSample' not in summary()[1]
    first = sample(pm, project)
    assert first['health'] == 'green'
    private = summary(initiator)[1]
    assert not private['materialsVisible'] and not private['canEditDraft']
    assert 'healthSample' not in private and 'manager' not in private
    assert data(initiator.get(project))['project']['stage'] == 'executing'

    milestone = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    data(pm.post(project + '/reports', headers=headers(), json=body(reportType='daily', period=datetime.now(TZ).date().isoformat(),
        structuredFields=sections(milestone_progress=[{'milestoneId': milestone, 'claim': 'progress'}]))))
    now = data(pm.get(project + '/health'))
    assert now['health'] == 'untrusted' and now['publication'] == 'withheld' and 'snapshotId' not in now
    meta, row = summary()
    expected = ('snapshotId', 'asOf', 'health', 'publication', 'ruleVersion')
    assert row['healthSample'] == {k: first[k] for k in expected}
    assert datetime.fromisoformat(meta['asOf']) > datetime.fromisoformat(first['asOf'])
    assert meta['sourceRefs'] == [f"project:{pid}@{row['revision']}",
                                  f"person:{row['manager']['personId']}@1", f"health-snapshot:{first['snapshotId']}"]
    assert data(pm.get(project + '/health/history'))['total'] == 1

    withheld = sample(pm, project)
    assert withheld['snapshotId'] != first['snapshotId']
    _, row = summary()
    assert row['healthSample'] == {k: withheld[k] for k in expected}
    assert row['healthSample']['health'] == 'untrusted'
    assert 'inputHash' not in row['healthSample'] and 'moneyVarianceRate' not in row['healthSample']

    # Recovered/legacy rows lacking the publication contract and future samples are not presented.
    s = db.health_snapshots
    with engine.begin() as c:
        old = dict(c.execute(sa.select(s).where(s.c.id == withheld['snapshotId'])).mappings().one())
        old.pop('id')
        c.execute(s.insert().values({**old, 'as_of': datetime.now(TZ) + timedelta(days=1)}))
        c.execute(s.insert().values({**old, 'as_of': datetime.now(TZ), 'results': {'health': 'green'}}))
    assert summary()[1]['healthSample']['snapshotId'] == withheld['snapshotId']
