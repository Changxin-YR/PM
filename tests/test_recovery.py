from datetime import UTC, date, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from hashlib import sha256
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
import sqlalchemy as sa

from pmo import evidence_storage, recovery, schema_v1 as db
from test_evidence import binary


@pytest.fixture
def recovery_db(tmp_path, monkeypatch):
    admin = recovery.database_url(os.environ['PMO_TEST_DATABASE_URL'])
    assert admin.host in ('127.0.0.1', '::1'), 'Pass a dedicated local test cluster only'
    if (admin.port, admin.username, admin.database) == (55439, 'pmo', 'pmo'):
        tools = recovery.PgTools('pmo104dev-db-1')
    elif shutil.which('pg_dump') and shutil.which('pg_restore'):
        tools = recovery.PgTools()
    else:
        pytest.fail('Install PostgreSQL client tools for the real recovery test')
    source = admin.set(database='pmo_backup_test_' + uuid4().hex)
    engine = sa.create_engine(source, hide_parameters=True)
    originals = tmp_path / 'source-evidence'; originals.mkdir(mode=0o700)
    monkeypatch.setenv('PMO_EVIDENCE_DIR', str(originals))
    with psycopg.connect(**recovery.connection_args(admin), autocommit=True) as control:
        control.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(source.database)))
        try:
            subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', '0016'], check=True,
                           env={**os.environ, 'DATABASE_URL': source.render_as_string(hide_password=False)})
            yield admin, source, engine, originals, tools, control
        finally:
            engine.dispose()
            control.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(source.database)))


def test_joint_recovery_keeps_only_snapshot_objects(recovery_db, tmp_path, monkeypatch):
    admin, source, engine, originals, tools, control = recovery_db
    png = binary('png')
    owner = uuid4()
    with engine.begin() as conn:
        org = conn.scalar(db.organizations.insert().values(name='Recovery fixture', status='active').returning(db.organizations.c.id))
        conn.execute(db.persons.insert().values(id=owner, created_by=owner, name='Fixture owner', org_id=org, is_active=True))
        project = conn.scalar(db.projects.insert().values(org_id=org, code='DRILL', name='Recovery fixture', stage='drafting',
                             revision=1, initiator_id=owner, created_by=owner).returning(db.projects.c.id))
        milestone = conn.scalar(db.milestones.insert().values(project_id=project, created_by=owner, name='Deliver', ordinal=1,
                                planned_date=date(2026, 10, 1), is_key=True).returning(db.milestones.c.id))

    def upload(content=png, resource='milestone', resource_id=milestone, name='record.png', mime='image/png'):
        key = uuid4().hex
        with engine.begin() as conn:
            eid = conn.scalar(db.evidence_files.insert().values(project_id=project, original_name=name, owner_id=owner,
                              uploaded_by=owner, sha256=sha256(content).hexdigest(), size_bytes=len(content), mime_type=mime,
                              storage_key=key, quarantine_status='pending_review').returning(db.evidence_files.c.id))
            conn.execute(sa.text("""INSERT INTO public.evidence_links (evidence_id, resource_type, resource_id, description)
                                  VALUES (:eid, :resource, :resource_id, 'Recovery fixture')"""),
                         dict(eid=eid, resource=resource, resource_id=resource_id))
            evidence_storage.store(content, key)
        return key

    original = upload()
    with engine.connect() as c:
        assert c.scalar(sa.text('SELECT version_num FROM alembic_version')) == '0016'
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True,
                   env={**os.environ, 'DATABASE_URL': source.render_as_string(hide_password=False)})
    book = json.dumps({'schemaVersion': 1, 'projectId': str(project), 'coveredThrough': datetime.now(UTC).isoformat(),
        'lines': [{'type': 'money', 'netActual': '100.00', 'sourceRef': 'SYNTHETIC-GL'},
                  {'type': 'effort', 'netActual': '10.00', 'sourceRef': 'SYNTHETIC-TIMESHEET'}]}).encode()
    external = upload(book, 'project', project, 'statement.json', 'application/json')
    orphan = uuid4().hex; evidence_storage.store(png, orphan)
    late = []
    run = tools.run

    def commit_after_snapshot(url, executable, args, **streams):
        if executable == 'pg_dump' and not late:
            late.append(upload())
        run(url, executable, args, **streams)

    monkeypatch.setattr(tools, 'run', commit_after_snapshot)
    output = tmp_path / 'points'
    targets = tmp_path / 'drill'
    point, manifest = recovery.backup(source, originals, output, tools)
    assert {obj.key for obj in manifest.objects} == {original, external}
    assert manifest.unreferencedCandidates == 2 and recovery.daily_points(output) == 1
    assert {entry.name for entry in (point / 'evidence').iterdir()} == {original, external}

    # Run the operator command end to end, not an in-process stand-in for pg_restore.
    cmd = [sys.executable, '-m', 'pmo.recovery']
    if tools.dev_container:
        cmd.append('--dev-compose')
    result = subprocess.run([*cmd, 'restore-drill', str(point), str(targets), '--trusted-backup'],
                            env={**os.environ, 'PMO_RESTORE_ADMIN_URL': admin.render_as_string(hide_password=False)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['result'] == 'verified' and receipt['objects'] == 2 and receipt['revision'] == manifest.revision
    name = receipt['database']
    assert name.startswith('pmo_restore_') and name != source.database
    try:
        with psycopg.connect(**recovery.connection_args(admin.set(database=name))) as restored:
            assert restored.execute('SELECT name FROM public.projects WHERE id = %s', (project,)).fetchone() == ('Recovery fixture',)
            assert restored.execute('SELECT count(*) FROM public.evidence_links WHERE resource_id = %s', (milestone,)).fetchone() == (1,)
            rows = restored.execute('''SELECT f.storage_key, f.sha256, f.size_bytes, l.resource_type, l.resource_id
                FROM public.evidence_files f JOIN public.evidence_links l ON l.evidence_id=f.id''').fetchall()
            assert {(r[0],r[3],r[4]) for r in rows} == {(original,'milestone',milestone), (external,'project',project)}
            assert restored.execute('SELECT version_num FROM public.alembic_version').fetchone() == (manifest.revision,)
            monkeypatch.setenv('PMO_EVIDENCE_DIR', str(targets / receipt['evidenceDirectory']))
            assert {r[0]: evidence_storage.checked_bytes(*r[:3]) for r in rows} == {original: png, external: book}
        assert {p.name for p in originals.iterdir()} == {original, external, orphan, late[0]}
    finally:
        control.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        monkeypatch.setenv('PMO_EVIDENCE_DIR', str(originals))

    def databases():
        return control.execute("SELECT datname FROM pg_database WHERE datname LIKE 'pmo_restore_%' ORDER BY datname").fetchall()

    before = databases()
    archived = point / 'evidence' / original
    archived.write_bytes(png[:-1] + bytes([png[-1] ^ 1]))
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_OBJECT_INTEGRITY_FAILED'):
        recovery.restore_drill(admin, point, tmp_path / 'bad-object', tools)
    assert databases() == before and not list((tmp_path / 'bad-object').iterdir())
    archived.write_bytes(png)
    with (point / 'database.dump').open('ab') as out:
        out.write(b'corrupt')
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_MANIFEST_OR_DUMP_MISMATCH'):
        recovery.restore_drill(admin, point, tmp_path / 'bad-dump', tools)
    assert databases() == before

    # A self-consistent checksum does not make an invalid PostgreSQL archive restorable.
    archive = point / 'database.dump'
    archive.write_bytes(b'not a PostgreSQL archive')
    broken = manifest.model_copy(update={'dump': recovery.digest(archive)})
    (point / 'manifest.json').write_text(broken.model_dump_json(), encoding='utf-8')
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_PG_RESTORE_FAILED'):
        recovery.restore_drill(admin, point, tmp_path / 'failed-restore', tools)
    assert databases() == before and not list((tmp_path / 'failed-restore').iterdir())

    (originals / original).unlink()
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_OBJECT_MISSING_OR_UNSAFE'):
        recovery.backup(source, originals, output, tools)
    assert [entry.name for entry in output.iterdir()] == [point.name]


def test_restore_rejects_remote_and_live_evidence_destination(tmp_path, monkeypatch):
    remote = recovery.database_url('postgresql://operator@restore.example.invalid/pmo')
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_TARGET_MUST_BE_ISOLATED_LOOPBACK'):
        recovery.restore_drill(remote, tmp_path / 'missing', tmp_path / 'restore', recovery.PgTools())
    monkeypatch.setenv('PMO_EVIDENCE_DIR', str(tmp_path / 'restore'))
    loopback = remote.set(host='127.0.0.1')
    point = Path(tmp_path / 'archive' / 'point')
    with pytest.raises(recovery.RecoveryError, match='RECOVERY_PATHS_OVERLAP'):
        recovery.restore_drill(loopback, point, tmp_path / 'restore' / 'nested', recovery.PgTools())
    assert not (tmp_path / 'restore').exists()
