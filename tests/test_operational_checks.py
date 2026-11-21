import json
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from fastapi.testclient import TestClient

from pmo.api import app
from pmo.health_probes import dependency_problem, expected_heads


def test_readiness_is_separate_from_liveness_and_requires_matching_schema(live):
    engine, api, _, _ = live
    assert api.get('/health/live').json() == {'status': 'live'}
    ready = api.get('/health/ready')
    assert (ready.status_code, ready.json()) == (200, {'status': 'ready'})
    assert ready.headers['Cache-Control'] == 'no-store' and ready.headers['X-Request-ID']
    # Only the disposable test schema is modified; production/shared alembic metadata is untouched.
    assert live[3]['PMO_SYNTHETIC_POLICY_SCHEMA'].startswith('pmo_test_')
    with engine.begin() as c:
        previous = c.scalar(sa.text('SELECT version_num FROM alembic_version'))
        assert previous in expected_heads()
        c.execute(sa.text("UPDATE alembic_version SET version_num='incompatible'"))
    try:
        response = api.get('/health/ready')
        assert (response.status_code, response.json()) == (503, {'status': 'not_ready'})
        assert api.get('/health/live').status_code == 200
    finally:
        with engine.begin() as c:
            c.execute(sa.text('UPDATE alembic_version SET version_num=:previous'), {'previous': previous})
    assert api.get('/health/ready').status_code == 200


def test_readiness_checks_private_directory(live, monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_URL', live[3]['DATABASE_URL'])
    from pmo.db import engine

    engine.cache_clear()
    try:
        missing = tmp_path / 'not-mounted'
        monkeypatch.setenv('PMO_EVIDENCE_DIR', str(missing))
        assert dependency_problem() == 'private_storage' and not missing.exists()
        monkeypatch.setenv('PMO_EVIDENCE_DIR', str(Path('web').resolve()))
        assert dependency_problem() == 'private_storage'
        monkeypatch.setenv('PMO_EVIDENCE_DIR', str(tmp_path))
        assert dependency_problem() is None
    finally:
        engine().dispose()
        engine.cache_clear()


def test_request_logs_use_only_fixed_schema_and_server_request_id(capsys):
    secret = 'never-log-' + uuid4().hex
    with TestClient(app) as client:
        invalid = client.post('/api/v1/auth/login', params={'note': secret}, json={'username': secret, 'password': secret},
            headers={'Authorization': f'Bearer {secret}', 'X-Request-ID': secret, 'Cookie': f'pmo_session={secret}'})
        missing = client.get('/api/v1/' + secret)
    assert invalid.status_code == 403 and missing.status_code == 404
    captured = capsys.readouterr().err
    assert secret not in captured
    entries = [json.loads(line) for line in captured.splitlines()]
    assert len(entries) == 2 and {e['operationId'] for e in entries} == {'auth_login', 'unmatched'}
    for entry, response in zip(entries, [invalid, missing], strict=True):
        assert set(entry) == {'at', 'event', 'level', 'requestId', 'operationId', 'status', 'durationMs', 'slow'}
        assert entry['event'] == 'http.request' and entry['durationMs'] >= 0
        assert (entry['requestId'], entry['status']) == (response.headers['X-Request-ID'], response.status_code)
