from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import httpx
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.rule_scan import scan_batch
from test_lifecycle import begin_execution, stage, upload
from test_reviews import data, headers, issue, member, team as team

COMMAND = {'desc':'Export a supplemental reconciliation worksheet', 'priority':'P1',
    'acceptanceCriteria':'Workbook lists each verified expense once', 'plannedConfirmAt':'2026-12-09',
    'reason':'Separate the new worksheet from the frozen initial deliverables.'}


def grant_reviewers(live, team):
    pid, people, _, _ = team
    with live[0].begin() as c:
        member(c,pid,people['pm'],people['initiator'],'reviewer')
        for who in ('pm','reviewer','sponsor'):
            c.execute(sa.text("""INSERT INTO change_approval_grants(project_id,person_id,target_type,authority_ref,expires_at)
                VALUES(:p,:u,'requirement','Independent synthetic test scope',clock_timestamp()+interval '1 hour')"""),
                {'p':pid,'u':people[who]})


def test_added_requirement_is_effective_only_after_independent_atomic_approval(live, team):
    pid, _, users, _ = team
    pm, reviewer, sponsor, customer = (users[who] for who in ('pm','reviewer','sponsor','client_representative'))
    project, initial, _ = begin_execution(live, team)
    grant_reviewers(live, team)
    path = project+'/changes/requirement-additions'
    metrics = project+'/requirement-metrics'
    signed = data(pm.get(project+'/requirement-baseline'))
    revision = stage(pm, project)['revision']
    key = headers(revision)
    assert pm.post(path,headers=headers(revision+1),json=COMMAND).json()['code']=='VERSION_CONFLICT'
    assert users['outsider'].post(path,headers=key,json=COMMAND).status_code==404
    assert reviewer.post(path,headers=key,json=COMMAND).status_code==403
    token = issue(pm,pid,['pmo_project_get','pmo_requirement_change_history'])
    auth = {'Authorization':'Bearer '+token}
    with httpx.Client(base_url=pm.base_url,trust_env=False) as agent:
        assert agent.post(path,headers={**auth,**key},json=COMMAND).status_code==403

    scan_batch(live[0],500)
    created = pm.post(path,headers=key,json=COMMAND)
    assert pm.post(path,headers=key,json=COMMAND).json()==created.json()
    assert pm.post(path,headers=key,json={**COMMAND,'priority':'P2'}).json()['code']=='IDEMPOTENCY_CONFLICT'
    assert pm.post(path,headers=headers(revision),json=COMMAND).json()['code']=='CHANGE_ALREADY_PENDING'
    request = data(created)
    assert request['status']=='pending' and request['before']['present'] is False
    with live[0].connect() as c:
        assert c.scalar(sa.text('SELECT dirty FROM project_rule_scan_state WHERE project_id=:p'),{'p':pid}) is True
    req, change = f"requirements/{request['targetId']}", f"changes/{request['changeId']}"
    assert data(pm.get(project+'/requirements'))['total']==1
    assert data(pm.get(metrics))['activeCount']==1 and data(pm.get(metrics))['approvedChangeCount']==0
    assert pm.get(req+'/changes').status_code==404
    assert data(reviewer.get(project+'/changes'))['decidableIds']==[request['changeId']]
    assert data(pm.get(project+'/changes'))['decidableIds']==[]
    assert pm.post(project+'/requirements',headers=headers(),json={k:v for k,v in COMMAND.items() if k!='reason'}).status_code==422
    verdict = {'rationale':'Approved after reviewing the separate scope and test criteria.'}
    assert pm.post(change+'/approve',headers=headers(1),json=verdict).json()['code']=='INDEPENDENT_APPROVER_REQUIRED'
    with httpx.Client(base_url=pm.base_url,trust_env=False) as agent:
        assert agent.post(change+'/approve',headers={**auth,**headers(1)},json=verdict).status_code==403
    eng = live[0]
    with eng.begin() as c:
        c.exec_driver_sql('CREATE TRIGGER addition_test_abort BEFORE INSERT ON outbox_events FOR EACH STATEMENT EXECUTE FUNCTION pmo_append_only()')
    attempt = headers(1)
    try:
        aborted=reviewer.post(change+'/approve',headers=attempt,json=verdict)
        assert (aborted.status_code,aborted.json()['code'])==(500,'DATABASE_FAILURE')
        assert data(pm.get(project+'/requirements'))['total']==1
        assert data(pm.get(project+'/changes'))['items'][0]['status']=='pending'
        with eng.connect() as c:
            bind={'p':pid,'change':UUID(request['changeId']),'request':aborted.headers['X-Request-ID']}
            assert c.execute(sa.text('''SELECT
                (SELECT status FROM approval_requests WHERE id=:change),
                (SELECT count(*) FROM change_decision_records WHERE change_id=:change),
                (SELECT count(*) FROM rule_events WHERE project_id=:p AND rule_id='LR-05' AND status='open'),
                (SELECT count(*) FROM audit_logs WHERE request_id=:request AND result='success'),
                (SELECT count(*) FROM outbox_events WHERE dedupe_key=:request)'''),bind).one()==('pending',0,1,0,0)
    finally:
        with eng.begin() as c: c.exec_driver_sql('DROP TRIGGER addition_test_abort ON outbox_events')

    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(lambda user: user.post(change+'/approve',headers=attempt,json=verdict),(reviewer,sponsor)))
    assert sorted(r.status_code for r in result)==[200,409]
    winner = 0 if result[0].is_success else 1
    saved = data(result[winner]); approver = (reviewer,sponsor)[winner]
    assert approver.post(change+'/approve',headers=attempt,json=verdict).json()==result[winner].json()
    assert saved['status']=='applied' and saved['revision']==3
    rows = data(pm.get(project+'/requirements'))['items']
    assert {row['requirementId'] for row in rows}=={str(initial),request['targetId']}
    added = next(row for row in rows if row['requirementId']==request['targetId'])
    assert added['baselineVersion']==1 and added['status']=='baseline'
    assert data(pm.get(req+'/changes'))['items']==[saved]
    final = data(pm.get(project+'/requirement-baseline'))
    assert (final['items'],final['contentHash'])==(signed['items'],signed['contentHash'])
    measured = data(pm.get(metrics))
    assert (measured['initialCount'],measured['activeWeight'],measured['approvedChangeCount'],measured['changeRate'])==(1,5,1,'0.000000')
    proof = upload(pm,reviewer,project,'requirement',request['targetId'])
    for action, actor in (('deliver',pm),('accept',reviewer),('customer-confirm',customer)):
        added = data(actor.post(req+'/'+action,headers=headers(added['revision']),json={'evidenceRef':proof['evidenceRef']}))
    assert added['status']=='customer_confirmed' and data(pm.get(metrics))['satisfaction']=='0.400000'


def test_rejected_addition_leaves_history_without_requirement(live, team):
    _, _, users, _ = team
    pm, reviewer = users['pm'], users['reviewer']
    project, _, _ = begin_execution(live,team)
    grant_reviewers(live,team)
    request=data(pm.post(project+'/changes/requirement-additions',headers=headers(stage(pm,project)['revision']),json=COMMAND))
    row=data(reviewer.post(f"changes/{request['changeId']}/reject",headers=headers(1),json={'rationale':'Duplicated by an existing deliverable.'}))
    assert row['status']=='rejected' and row['targetId']==request['targetId']
    assert data(pm.get(project+'/changes'))['items']==[row]
    assert data(pm.get(project+'/requirements'))['total']==1
    assert data(pm.get(project+'/requirement-metrics'))['approvedChangeCount']==0
