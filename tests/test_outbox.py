import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import sqlalchemy as sa

from pmo import outbox, schema_v1 as tables


@pytest.fixture
def queue_db():
    admin = sa.create_engine(os.environ['PMO_TEST_DATABASE_URL'], hide_parameters=True)
    schema = 'pmo_queue_test_'+uuid4().hex
    with admin.begin() as c:
        c.exec_driver_sql(f'CREATE SCHEMA {schema}')
    db = sa.create_engine(admin.url.update_query_dict({'options':f'-csearch_path={schema}'}), hide_parameters=True)
    env = {**os.environ, 'DATABASE_URL':db.url.render_as_string(hide_password=False)}
    def migrate(*args, check=True):
        return subprocess.run([sys.executable,'-m','alembic',*args],env=env,check=check,capture_output=True,text=True)
    try:
        migrate('upgrade','0005')
        with db.begin() as c:
            org = c.scalar(tables.organizations.insert().values(name='Queue QA',status='active').returning(tables.organizations.c.id))
            person = uuid4()
            c.execute(tables.persons.insert().values(id=person,created_by=person,name='Queue QA',org_id=org,is_active=True))
            pid = c.scalar(tables.projects.insert().values(org_id=org,code='QUEUE',name='Queue QA',created_by=person,initiator_id=person,stage='drafting',revision=1).returning(tables.projects.c.id))
        def enqueue(payload):
            with db.begin() as c:
                return c.scalar(tables.outbox_events.insert().values(dedupe_key=uuid4().hex,event_type='project.created',payload=payload,status='pending').returning(tables.outbox_events.c.id))
        eid = enqueue({'projectId':str(pid)})
        migrate('upgrade','head')
        yield db, env, eid, enqueue, migrate
    finally:
        db.dispose()
        with admin.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        admin.dispose()


def test_nonempty_upgrade_and_restart_after_consume(queue_db):
    db, env, eid, _, migrate = queue_db
    with db.connect() as c:
        assert c.execute(sa.text('SELECT role,assignment_status FROM project_members')).one()==('initiator','accepted')
    with ThreadPoolExecutor(2) as pool:
        leases = list(pool.map(outbox.claim,[db,db]))
    lease, = [item for item in leases if item]
    assert lease.event_id == eid and lease.attempt == 1
    assert not outbox.acknowledge(db,lease)
    assert outbox.consume(db,lease) and outbox.consume(db,lease)
    with db.begin() as c:
        c.execute(sa.text("UPDATE outbox_events SET locked_until=now()-interval '1 second'"))
    # New process has no knowledge of the original lease or its successful delivery.
    subprocess.run([sys.executable,'-m','pmo.outbox','--limit','1'],env=env,check=True)
    assert not outbox.consume(db,lease) and not outbox.acknowledge(db,lease)
    with db.connect() as c:
        assert c.execute(sa.text('SELECT status,retry_count,published_at IS NOT NULL FROM outbox_events')).one() == ('sent',1,True)
        assert c.scalar(sa.text('SELECT count(*) FROM project_event_inbox')) == 1
        assert c.scalar(sa.text('SELECT outcome FROM outbox_attempts')) == 'sent'
    result = migrate('downgrade','0005',check=False)
    assert result.returncode and 'Refusing destructive downgrade' in result.stderr


def test_stale_worker_backoff_and_dead_letter(queue_db):
    db, env, _, enqueue, _ = queue_db
    obsolete = outbox.claim(db)
    with db.begin() as c:
        c.execute(sa.text("UPDATE outbox_events SET locked_until=now()-interval '1 second'"))
    live = outbox.claim(db)
    assert live.attempt == 2 and not outbox.consume(db,obsolete)
    assert outbox.consume(db,live) and outbox.acknowledge(db,live)
    eid = enqueue({'projectId':'private invalid payload'})
    for attempt in range(1,outbox.MAX_ATTEMPTS+1):
        assert outbox.dispatch(db,1) == 0
        with db.begin() as c:
            status,code,waiting,count = c.execute(sa.text('SELECT status,last_error,next_attempt_at>now(),retry_count FROM outbox_events WHERE id=:id'),{'id':eid}).one()
            assert code == 'INVALID_PROJECT_EVENT' and waiting and count == attempt
            assert status == ('dead_letter' if attempt==outbox.MAX_ATTEMPTS else 'pending')
            assert outbox.claim(db) is None
            c.execute(sa.text('UPDATE outbox_events SET next_attempt_at=now() WHERE id=:id'),{'id':eid})
    result = subprocess.run([sys.executable,'-m','pmo.outbox','--dead-letters'],env=env,capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)[0]['id'] == str(eid) and 'private' not in result.stdout
    with db.connect() as c:
        assert c.scalar(sa.text('SELECT count(*) FROM project_event_inbox')) == 1
        assert c.scalar(sa.text("SELECT count(*) FROM outbox_attempts WHERE outcome='lease_expired'")) == 1
