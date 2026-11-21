from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.reports import CONFIRM, GENERATE, GET, LIST, reports
from test_facts import fact_path, medium, post, proposal, report_path, submit
from test_reports import WEEK, _token, body, data, deny, headers, sections, team as team


def generate(pm, path, *, key=None, period=WEEK):
    return pm.post(path + '/weekly-drafts', headers=key or headers(1), json={'period':period})


def test_generation_edit_publish_sources_and_cost_once(team):
    pm, reviewer, second = (team.users[k] for k in ('pm', 'reviewer', 'reporter'))
    with team.engine.begin() as c:
        for who in ('reviewer', 'reporter'):
            c.execute(db.user_roles.insert().values(user_id=team.people[who].account, role='finance_verifier'))
    proof = {'reason':'Reconciled synthetic worksheet entry', 'occurredAt':'2024-12-29T16:00:00Z'}

    def posting(ref, amount, *, occurred=proof['occurredAt'], kind='effort', verified=True):
        row = data(pm.post(f'projects/{team.pid}/cost-postings', headers=headers(), json={
            'type':kind, 'amount':amount, 'period':occurred[:7], 'sourceRef':ref}))
        if verified:
            row = data(reviewer.post(f"cost-postings/{row['postingId']}/verify", headers=headers(1),
                json={**proof, 'occurredAt':occurred}))
        return row['postingId']

    current = posting('timesheet-opening', '3.10')
    original = posting('timesheet-correction', '1.10')
    end = posting('timesheet-next-week', '7.00', occurred='2025-01-05T16:00:00Z')
    pending = posting('timesheet-unconfirmed', '8.00', verified=False)
    money = posting('money-not-person-days', '900.00', kind='money')
    minus = data(reviewer.post(f'cost-postings/{original}/reverse', headers=headers(2), json={'reason':'Incorrect time sheet line'}))
    data(second.post(f"cost-postings/{minus['postingId']}/verify", headers=headers(1), json={'reason':'Independent reconciliation'}))
    report = data(pm.post(team.path, headers=headers(), json=body(structuredFields=sections(effort_person_days='2.50',
        milestone_progress=[{'milestoneId':str(team.milestones[0]), 'claim':'completion'}]))))
    note = data(medium(reviewer, data(post(pm, report))))
    data(post(pm, report, proposal(report, subject='Unverified candidate')))
    risk = c_risk(team)

    replay = headers(1)
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda _: generate(pm, team.path, key=replay), range(2)))
    result = data(outcomes[0])
    assert outcomes[0].json() == outcomes[1].json()
    assert result['status']=='draft' and not result['isCurrentConfirmed']
    assert data(pm.get(team.path))['weeklyGenerationRevision']==1
    assert 'weeklyGenerationRevision' not in data(reviewer.get(team.path))
    deny(generate(pm, team.path), 409, 'WEEKLY_DRAFT_ALREADY_CURRENT')
    deny(generate(pm, team.path, key=replay, period='2025-W02'), 409, 'IDEMPOTENCY_CONFLICT')

    basis, fields = result['weeklyBasis'], result['structuredFields']
    assert basis['verifiedPeriodPersonDays']==fields['effort_person_days']=='3.10'
    assert basis['verifiedPeriodPostingCount']==3
    refs = set(basis['verifiedPeriodPostingRefs'])
    assert refs == {f'cost-posting:{item}' for item in (current, original, minus['postingId'])}
    assert not refs.intersection(f'cost-posting:{item}' for item in (end,pending,money))
    fact, = basis['verifiedFacts']
    assert fact['sourceRef']==fact_path(note).replace('facts/','fact:')+'@2'
    assert fact['quote']=='2.50' and fact['reportHash']==report['contentHash']
    assert basis['health']['health']=='untrusted' and 'timeLine' not in basis['health']
    assert 'claim' not in fields['milestone_progress'][0]  # A submitted claim is not completion evidence.
    assert fields['risks_and_blockers']==[{'riskId':str(risk), 'summary':'阻塞：Synthetic open risk'}]
    path = report_path(result)
    deny(reviewer.post(path+'/confirm',headers=headers(1),json={}),404,'RESOURCE_NOT_FOUND')
    edit = data(pm.patch(path, headers=headers(1), json={'structuredFields':{**fields, 'effort_person_days':'6.00'},
        'narrative':'PM verified the weekly narrative.'}))
    assert edit['revision']==2 and edit['weeklyBasis']==basis and edit['contentHash']!=result['contentHash']
    saved = data(pm.post(path+'/submit', headers=headers(2), json={}))
    assert saved['status']=='submitted' and not saved['isCurrentConfirmed']
    deny(reviewer.post(path+'/confirm',headers=headers(3),json={}),403,'FORBIDDEN')
    deployed = data(pm.post(path+'/confirm', headers=headers(3), json={}))
    assert deployed['isCurrentConfirmed'] and deployed['confirmedBy']==str(team.people['pm'].id)
    assert data(pm.get(path))==deployed and deployed['weeklyBasis']==basis
    assert data(pm.get(f'projects/{team.pid}/costs'))['effort']['actual']=='10.10'
    with httpx.Client(base_url=team.url, trust_env=False) as agent:
        token = _token(team, person='pm', actions=(LIST, GET, GENERATE, CONFIRM))
        assert 'weeklyBasis' not in data(agent.get(path, headers=token))
        deny(agent.post(team.path+'/weekly-drafts', headers={**token, **headers(1)},json={'period':WEEK}),403,'FORBIDDEN')
        deny(agent.post(path+'/confirm', headers={**token, **headers(4)},json={}),403,'FORBIDDEN')
    with pytest.raises(sa.exc.DBAPIError, match='Weekly report source receipts'), team.engine.begin() as c:
        c.execute(reports.update().where(reports.c.id==UUID(result['reportId'])).values(weekly_basis=None))


def c_risk(team):
    with team.engine.begin() as c:
        return c.scalar(db.risks.insert().values(project_id=team.pid, created_by=team.people['pm'].id,
            statement='Synthetic open risk', prob=2, impact=4, is_blocking=True, response='Review', status='open')
            .returning(db.risks.c.id))


def test_cross_scope_broken_publication_receipt_and_conflict(team):
    pm, reviewer, outsider = (team.users[k] for k in ('pm','reviewer','outsider'))
    deny(generate(outsider, team.path), 404, 'RESOURCE_NOT_FOUND')
    deny(generate(reviewer, team.path), 403, 'FORBIDDEN')
    deny(generate(pm, team.path, key=headers(2)),409,'VERSION_CONFLICT')
    deny(generate(pm, team.path, period='2025-W53'),422,'VALIDATION_FAILED')
    deny(generate(pm, team.path, period='2099-W01'),422,'REPORT_PERIOD_IN_FUTURE')
    primary = submit(pm, team.path)
    data(medium(reviewer, data(post(pm, primary))))
    draft = data(generate(pm, team.path))
    assert len(draft['weeklyBasis']['verifiedFacts'])==1
    assert 'effort_person_days' not in draft['structuredFields'] and draft['weeklyBasis']['verifiedPeriodPostingCount']==0
    path = report_path(draft)
    edit = data(pm.patch(path, headers=headers(1), json={'structuredFields':{**draft['structuredFields'], 'effort_person_days':'0'}}))
    data(pm.post(path+'/submit', headers=headers(edit['revision']), json={}))
    with team.engine.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER weekly_test_abort BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    attempt = headers(3)
    try:
        failed = pm.post(path+'/confirm', headers=attempt, json={})
        deny(failed,500,'DATABASE_FAILURE')
        kept = data(pm.get(path))
        assert kept['status']=='submitted' and kept['revision']==3 and not kept['isCurrentConfirmed']
        with team.engine.connect() as c:
            assert not c.scalar(sa.select(db.audit_logs.c.id).where(
                db.audit_logs.c.request_id==failed.headers['X-Request-ID'], db.audit_logs.c.result=='success'))
            assert not c.scalar(sa.select(db.idempotency_keys.c.id).where(db.idempotency_keys.c.idempotency_key==attempt['Idempotency-Key']))
    finally:
        with team.engine.begin() as c:
            c.exec_driver_sql('DROP TRIGGER weekly_test_abort ON outbox_events')

    contradictory = submit(pm, team.path, amount='3.80')
    fact = data(post(pm, contradictory, proposal(contradictory, '3.80')))
    assert fact['validationStatus']=='disputed'
    deny(pm.post(path+'/confirm', headers=attempt, json={}),409,'WEEKLY_DRAFT_SOURCE_CHANGED')
    regenerated = data(generate(pm, team.path))
    assert regenerated['weeklyBasis']['verifiedFacts']==[]
    assert regenerated['weeklyBasis']['sourceHash']!=draft['weeklyBasis']['sourceHash']
    assert data(pm.get(path))['weeklyBasis']==draft['weeklyBasis']  # History is not rewritten.
