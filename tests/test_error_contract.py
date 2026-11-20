from uuid import UUID, uuid4

import sqlalchemy as sa

from pmo import schema_v1 as db


def test_request_ids_and_no_reflected_validation_input(live,actor):
    engine, api, _, _ = live
    secret, _ = actor()
    inbound = str(uuid4())
    h = {'Authorization':'Bearer '+secret, 'Idempotency-Key':uuid4().hex, 'X-Request-ID':inbound}
    payload = {'code':'AUDIT-ID','name':'Request correlation'}
    success = api.post('/api/v1/projects', headers=h, json=payload)
    rid = success.headers['X-Request-ID']
    assert UUID(rid) and rid!=inbound and success.headers['cache-control']=='no-store'
    with engine.connect() as c:
        assert c.scalar(sa.select(db.audit_logs.c.result).where(db.audit_logs.c.request_id==rid))=='success'
    denied=api.post('/api/v1/projects', headers=h, json={**payload,'name':'Different'})
    assert denied.status_code==409 and denied.json()['requestId']==denied.headers['X-Request-ID']!=rid
    with engine.connect() as c:
        assert c.scalar(sa.select(db.audit_logs.c.error_code).where(db.audit_logs.c.request_id==denied.json()['requestId']))=='IDEMPOTENCY_CONFLICT'
    for invalid in [api.post('/api/v1/auth/login',json={'username':'x','password':{'bad':secret}}),
                    api.post('/api/v1/projects',headers=h,json={**payload,'unrecognized':secret})]:
        assert invalid.status_code==422 and secret not in invalid.text
        assert invalid.json()['requestId']==invalid.headers['X-Request-ID']
