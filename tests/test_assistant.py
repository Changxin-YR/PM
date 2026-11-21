"""Four read-only workspace modes against the real PostgreSQL and HTTP stack."""
from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa

from pmo.health_calculation import TZ, week_key
from test_facts import medium, post, proposal, report_path, submit
from test_health import code, prepare, sample
from test_reports import body, sections
from test_reviews import bench, data, decide, headers, score, start_charter, team as team


def test_charter_only_reuses_published_assessment_and_existing_visibility(live, team):
    pid, _, users, agent = team
    pm, reviewer, outsider = (users[key] for key in ('pm', 'reviewer', 'outsider'))
    base = f'projects/{pid}'
    path = base + '/assistant/'
    initial = data(pm.get(path + 'charter'))
    assert initial['scoreSummary'] is None and initial['lessonRefs'] == []
    assert initial['completeness'] == data(pm.get(base + '/gates/check', params={'gate': 'charter'}))
    assert any(item['code'] == 'CHARTER_MISSING' for item in initial['completeness']['missing'])
    api = live[1]
    assert data(api.get('/api/v1/' + base, headers=agent))['project']['projectId'] == pid
    for mode in ('charter', 'process', 'closing', 'ledger'):
        code(outsider.get(path + mode), 404, 'RESOURCE_NOT_FOUND')
        code(api.get('/api/v1/' + path + mode, headers=agent), 403, 'FORBIDDEN')
        code(api.get('/api/v1/' + path + mode), 401, 'UNAUTHORIZED')
    code(reviewer.get(path + 'process'), 403, 'FORBIDDEN')
    code(pm.get(path + 'ledger', params={'topic': 'write_score'}), 422, 'VALIDATION_FAILED')

    review = start_charter(pid, users)
    score(reviewer, review, [3, 4, 3, 4])
    assert bench(reviewer, review)['scores']
    for person in (pm, reviewer, users['sponsor']):
        queued = data(person.get(path + 'charter'))
        assert queued['completeness']['eligible'] and queued['scoreSummary'] is None
        assert not queued['weaknesses'] and 'scores' not in queued
    decision = data(decide(reviewer, review))
    answer = data(pm.get(path + 'charter'))
    received = answer['scoreSummary']
    assert received['total'] == '3.55'
    assert {key: received[key] for key in decision['assessment']} == decision['assessment']
    assert received['sourceRefs'][0].startswith(f"decision:{decision['decision']['decisionId']}@")
    assert len(answer['weaknesses']) == 2
    assert all(item['sourceRefs'] == received['sourceRefs'] for item in answer['weaknesses'])


def test_process_and_ledger_use_trusted_sources_then_withhold_conflicts(live, team):
    pid, _, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    project, _ = prepare(live, team)
    path = project + '/assistant/'
    current = sample(pm, project)
    answer = data(pm.get(path + 'ledger'))
    assert answer['asOf'] == current['asOf']
    assert [item['text'].split('：', 1)[1] for item in answer['answer']] == [current[name] for name in (
        'timeLine', 'moneyVarianceRate', 'effortVarianceRate', 'requirementSatisfactionRate')]
    assert all(item['sourceRefs'] == current['sourceRefs'] for item in answer['answer'])
    assert answer['unknowns'] == []
    search = {'topic': 'fact', 'keyword': 'Synthetic delivery'}
    assert data(pm.get(path + 'ledger', params=search))['answer'] == []

    report = submit(pm, project + '/reports')
    candidate = data(post(pm, report))
    assert data(pm.get(path + 'ledger', params=search))['answer'] == []
    trusted = data(medium(reviewer, candidate))
    quote, = data(pm.get(path + 'ledger', params=search))['answer']
    assert quote['text'] == '2.50' and quote['sourceRefs'] == [
        f"fact:{trusted['factId']}@{trusted['revision']}", trusted['sourceRef']]
    # Matching the key is not enough to overwrite a previously verified fact.
    conflict = submit(pm, project + '/reports', amount='3.80', period='2025-W02')
    data(post(pm, conflict, proposal(conflict, '3.80')))
    hidden = data(pm.get(path + 'ledger', params=search))
    assert hidden['answer'] == [] and hidden['unknowns']
    analysis = data(pm.post(report_path(conflict) + '/analyze', headers=headers(conflict['revision']), json={}))
    assert analysis['status'] == 'manual'

    process = data(pm.get(path + 'process'))
    assert process['reportId'] == conflict['reportId']
    assert process['analysis']['runId'] == analysis['runId'] and not process['modelEnabled']
    assert process['factCandidates'] == []
    assert len(process['conflicts']) == 1 and process['conflicts'][0]['sourceRefs'][0].startswith('fact-clarification:')
    assert process['weeklyDraft']['verifiedPersonDays'] is None
    period = process['weeklyDraft']['period']
    assert period == week_key(datetime.now(TZ).date())
    assert set(process['weeklyDraft']['structuredFields']) == {
        'milestone_progress', 'effort_person_days', 'requirement_progress', 'risks_and_blockers'}
    with live[0].connect() as c:
        assert c.scalar(sa.text('SELECT count(*) FROM reports WHERE project_id=:pid AND status=\'draft\''), {'pid': pid}) == 0
    saved = data(pm.post(project + '/reports/weekly-drafts', headers=headers(data(pm.get(project))['project']['revision']),
                         json={'period': period}))
    assert saved['weeklyBasis']['sourceHash'] == process['weeklyDraft']['sourceHash']

    m = data(pm.get(project + '/milestones'))['items'][0]['milestoneId']
    data(pm.post(project + '/reports', headers=headers(), json=body(period=period,
        structuredFields=sections(milestone_progress=[{'milestoneId': m, 'claim': 'progress'}]))))
    latest = data(pm.get(path + 'ledger'))
    assert latest['answer'] == [] and any('不可信' in reason for reason in latest['unknowns'])
    assert f"health-snapshot:{current['snapshotId']}" not in latest['sourceRefs']


def test_ledger_lesson_never_crosses_project_membership(live, team):
    from test_health import close_for_lr08
    from test_lifecycle import ANSWERS, execute, propose
    from test_reviews import member

    pid, people, users, _ = team
    project, _ = close_for_lr08(live, team)
    pm, reviewer = users['pm'], users['reviewer']
    data(execute(pm, project, data(propose(pm, project, 'retrospective'))))
    retro = data(pm.get(project + '/retrospective'))
    receipt = data(pm.put(project + '/retrospective', headers=headers(retro['revision']), json={'answers': ANSWERS}))
    closing = data(pm.get(project + '/assistant/closing'))
    assert closing['retrospectiveKind'] == 'normal' and len(closing['retrospectiveQuestions']) == 4
    assert closing['lessonDrafts'][0]['text'] == ANSWERS[-1]
    assert closing['lessonDrafts'][0]['sourceRefs'] == [f"retrospective:{receipt['retrospectiveId']}@{receipt['revision']}"]
    assert data(pm.get(project + '/lessons'))['items'] == []
    lesson = data(pm.post(project + '/lessons', headers=headers(), json={'category': 'delivery',
        'what': 'Preserve supplier evidence', 'rootCause': 'Delivery signoff precedes invoice', 'advice': 'Verify originals'}))
    matches = {'topic': 'lesson', 'keyword': 'supplier', 'category': 'delivery'}
    assert data(pm.get(project + '/assistant/ledger', params=matches))['answer'] == []
    lesson_id = lesson['lessonId']
    confirmed = data(reviewer.post(f'lessons/{lesson_id}/confirm', headers=headers(lesson['revision']), json={}))
    past = data(pm.get(project + '/assistant/ledger', params=matches))['answer']
    assert len(past) == 1 and f"lesson:{lesson_id}@{confirmed['revision']}" in past[0]['sourceRefs']

    next_draft = data(pm.post('projects', headers=headers(), json={'code': 'NEW-' + uuid4().hex[:8], 'name': 'Next charter'}))
    next_path = f"projects/{next_draft['projectId']}/assistant/charter"
    selected = data(pm.get(next_path, params={'keyword': 'supplier', 'category': 'delivery'}))['lessonRefs']
    assert [item['lessonId'] for item in selected] == [lesson_id] and selected[0]['projectId'] == pid
    # Only the current draft is shared with this other applicant; its charter must not disclose the earlier project.
    with live[0].begin() as c:
        member(c, next_draft['projectId'], people['client_representative'], people['pm'], 'member')
        c.execute(sa.text('''UPDATE project_members SET revoked_at=now(),assignment_status='revoked'
            WHERE person_id=:person AND project_id=:pid'''),
                  {'person': people['client_representative'], 'pid': pid})
    assert data(users['client_representative'].get(next_path, params={'keyword': 'supplier'}))['lessonRefs'] == []
