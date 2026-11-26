"""Verify the opt-in JSON chat compatibility contract and protected configuration split."""
import json
import sys
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from pmo import model_gateway
from pmo.assistant_model import PROFILES
from scripts import pilot_local
from test_report_analysis import answer, gateway


def completion(value, **updates):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(value), **updates}}]}


@pytest.fixture
def configured_pilot(monkeypatch, tmp_path):
    monkeypatch.setattr(pilot_local, 'HOME', tmp_path)
    monkeypatch.setattr(pilot_local, 'private_home', lambda: None)
    pilot_local.save('pilots.json', {code: {'id': str(uuid4())} for code in pilot_local.PILOT_CODES})
    return {'url': 'https://provider.example.invalid/v1/chat/completions', 'protocol': 'chat_completions_json',
            'version': 'fixture-model', 'token': 'fixture-private-key', 'projects': ['SIM-EXEC-001']}


@pytest.mark.parametrize('path', ('/', '/v1', '/api/v1/'))
def test_chat_endpoint_needs_request_path_in_pilot_and_runtime(monkeypatch, configured_pilot, path):
    url = f'https://provider.example.invalid{path}'
    pilot_local.save('model-assistant.json', configured_pilot | {'url': url})
    with pytest.raises(pilot_local.ModelConfigError, match='complete Chat Completions request URL'):
        pilot_local.model_env(True)
    monkeypatch.setenv('PMO_ASSISTANT_PREVIEWS', '1')
    monkeypatch.setenv('PMO_ASSISTANT_MODEL_PROJECTS', str(uuid4()))
    monkeypatch.setenv('PMO_ASSISTANT_MODEL_PROTOCOL', 'chat_completions_json')
    monkeypatch.setenv('PMO_ASSISTANT_MODEL_VERSION', 'fixture-model')
    monkeypatch.setenv('PMO_ASSISTANT_MODEL_URL', url)
    assert model_gateway.Gateway.for_previews() is None
    monkeypatch.setenv('PMO_ASSISTANT_MODEL_URL', configured_pilot['url'])
    assert model_gateway.Gateway.for_previews() is not None


def test_model_preflight_distinguishes_saved_loaded_worker_and_provider(monkeypatch, configured_pilot):
    pilot_local.save('model-assistant.json', configured_pilot | {'protocol': 'pmo_json', 'url': 'https://provider.example.invalid/v1'})
    pilot_local.save('model-report.json', configured_pilot | {'token': '', 'projects': ['SIM-NEW-001']})
    _, _, loaded = pilot_local.model_env(True)
    loaded['assistant']['version'] = 'prior-assistant-version'
    pids = {'api': 11, 'web': 22, 'outbox': 33, 'reportWorker': 42}
    pilot_local.save('runtime.json', {'web': pilot_local.WEB, 'api': pilot_local.API, 'models': loaded, 'pids': pids})
    monkeypatch.setattr(pilot_local, 'ready', lambda: False)
    monkeypatch.setattr(pilot_local, 'probe_runtime_processes', lambda _: pytest.fail('processes checked for a stale runtime'))
    stale = pilot_local.model_preflight()
    assert not stale['running']
    assert stale['scheduler'] == 'not_recorded'
    assert all(not channel['loaded'] for channel in stale['channels'].values())
    assert stale['channels']['report']['worker'] == 'not_loaded'

    monkeypatch.setattr(pilot_local, 'ready', lambda: True)
    observed = []
    monkeypatch.setattr(pilot_local, 'probe_runtime_processes', lambda processes: observed.append(processes) or set(pids))
    active = pilot_local.model_preflight()
    assert active['running'] and observed == [pids]
    assistant, report = (active['channels'][name] for name in ('assistant', 'report'))
    assert assistant['configuration'] == 'valid' and assistant['loaded'] and assistant['tokenPresent']
    assert 'exact base-looking URL' in assistant['guidance']
    assert assistant['version'] == 'fixture-model' and assistant['loadedConfiguration']['version'] == 'prior-assistant-version'
    assert report['configuration'] == 'valid' and report['loaded'] and not report['tokenPresent']
    assert report['worker'] == 'running' and report['projects'] == ['SIM-NEW-001']
    assert assistant['providerResult'] == report['providerResult'] == 'not_checked'
    rendered = json.dumps(active)
    assert configured_pilot['token'] not in rendered and 'provider.example.invalid' not in rendered

    monkeypatch.setattr(pilot_local, 'probe_runtime_processes', lambda _: set(pids) - {'reportWorker'})
    assert pilot_local.model_preflight()['channels']['report']['worker'] == 'unavailable'
    monkeypatch.setattr(pilot_local, 'probe_runtime_processes', lambda _: None)
    unverified = pilot_local.model_preflight()
    assert unverified['servicesReady'] and not unverified['running']
    assert all(not channel['loaded'] for channel in unverified['channels'].values())


@pytest.mark.parametrize('active', (False, True))
def test_pilot_supervises_scheduler_under_scoped_api_environment(monkeypatch, configured_pilot, active):
    cfg = {'id': uuid4().hex, 'passwords': {'api': 'fixture-api', 'outbox': 'fixture-outbox'}}
    pilot_local.save('model-assistant.json', configured_pilot)
    pilot_local.save('model-report.json', configured_pilot)
    connection = SimpleNamespace(execute=lambda *args: SimpleNamespace(
        fetchone=lambda: (pilot_local.DB_NAME, pilot_local.schema(cfg), 'pmo_api')))
    monkeypatch.setenv('PMO_REPORT_MODEL_TOKEN', 'untrusted-inherited-secret')
    monkeypatch.setattr(pilot_local, 'settings', lambda: cfg)
    monkeypatch.setattr(pilot_local, 'verify_database', lambda value: None)
    monkeypatch.setattr(pilot_local, 'db_connect', lambda cfg, role: nullcontext(connection))
    monkeypatch.setattr(pilot_local, 'bind', lambda port: nullcontext())
    monkeypatch.setattr(pilot_local, 'ready', lambda: True)
    monkeypatch.setattr(pilot_local, 'web_artifact_digest', lambda: 'fixture-web-index-sha')
    monkeypatch.setattr(pilot_local, 'activation_scope', lambda *_: ({}, 'unit-test-permit'))
    monkeypatch.setenv('PMO_PILOT_MODEL_PERMIT', 'unit-test-permit')
    monkeypatch.setattr(pilot_local.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(pilot_local.subprocess, 'check_output', lambda *args, **kwargs: 'synthetic-head\n')
    processes = {}
    snapshots = {}

    class Child:
        def __init__(self, pid, command, env):
            self.pid, self.command, self.env = pid, command, env
            self.terminated = False
            self.waited = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            assert self.terminated and timeout == 12
            self.waited = True

    def start(command, *, env, **kwargs):
        child = Child(len(processes) + 100, command, env)
        processes[child.pid] = child
        return child

    save = pilot_local.save

    def publish(name, runtime):
        save(name, runtime)
        snapshots.update(runtime)
        pilot_local.local_file('stop.request').touch()

    monkeypatch.setattr(pilot_local.subprocess, 'Popen', start)
    monkeypatch.setattr(pilot_local, 'save', publish)
    pilot_local.serve(active)
    pids = snapshots['pids']
    scanner = processes[pids['scheduler']]
    assert scanner.command == [str(pilot_local.PYTHON), '-m', 'pmo.rule_scan', '--watch']
    assert scanner.env == pilot_local.env_base() | {
        'DATABASE_URL': pilot_local.database(cfg, 'api'),
        'PMO_SYNTHETIC_POLICY_SCHEMA': pilot_local.schema(cfg),
        'PMO_EVIDENCE_DIR': str(pilot_local.local_file('private-evidence')),
    }
    assert processes[pids['outbox']].env['DATABASE_URL'] == pilot_local.database(cfg, 'outbox')
    assert ('reportWorker' in pids) == active
    assert set(snapshots['models']) == (set(pilot_local.CHANNELS) if active else set())
    assert all(child.terminated and child.waited for child in processes.values())
    assert not pilot_local.local_file('runtime.json').exists()


@pytest.mark.parametrize(('scheduler', 'expected'), ((True, 'running'), (False, 'unavailable'), (None, 'not_recorded')))
def test_status_scheduler_is_additive_for_legacy_and_current_runtime(monkeypatch, configured_pilot, capsys, scheduler, expected):
    pids = {'api': 11, 'web': 22, 'outbox': 33}
    if scheduler is not None:
        pids.update(scheduler=44, supervisor=55)
    pilot_local.save('runtime.json', {'web': pilot_local.WEB, 'api': pilot_local.API, 'pids': pids})
    active = set(pids) - ({'scheduler'} if not scheduler else set())
    monkeypatch.setattr(pilot_local, 'ready', lambda: True)
    monkeypatch.setattr(pilot_local, 'probe_runtime_processes', lambda processes: active)
    monkeypatch.setattr(sys, 'argv', ['pilot_local', 'status'])
    pilot_local.main()
    status = json.loads(capsys.readouterr().out)
    assert status['running'] and status['scheduler'] == expected
    assert status['matchedProcesses'] == sorted(active)
    preflight = pilot_local.model_preflight()
    assert preflight['running'] and preflight['scheduler'] == expected


@pytest.mark.parametrize(('changes', 'message'), [
    ({'url': 'https://embedded-secret@provider.example.invalid/v1/chat/completions'}, 'embedded credentials'),
    ({'protocol': ['pmo_json']}, 'Choose pmo_json'),
    ({'version': ' '}, 'model ID or version'),
    ({'projects': ['SIM-EXEC-001', 'SIM-EXEC-001']}, 'unique synthetic'),
    ({'projects': ['outside']}, 'unique synthetic'),
    ({'token': 'invalid bearer secret'}, 'valid bearer token'),
])
def test_invalid_model_configuration_reports_safe_per_channel_error(configured_pilot, changes, message):
    pilot_local.save('model-assistant.json', configured_pilot | changes)
    channels = pilot_local.model_preflight()['channels']
    assert channels['assistant']['configuration'] == 'invalid' and message in channels['assistant']['error']
    assert channels['report']['configuration'] == 'missing'
    rendered = json.dumps(channels)
    assert all(value not in rendered for value in ('embedded-secret', 'fixture-private-key', 'invalid bearer secret'))
    with pytest.raises(pilot_local.ModelConfigError, match=message):
        pilot_local.model_env(True)


def test_model_preflight_cli_detects_malformed_config_and_missing_project(monkeypatch, configured_pilot, capsys):
    pilot_local.save('pilots.json', {'SIM-EXEC-001': {'id': 'malformed-uuid'}})
    pilot_local.save('model-assistant.json', configured_pilot)
    pilot_local.local_file('model-report.json').write_text('malformed JSON fixture-secret', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['pilot_local', 'model-preflight'])
    with pytest.raises(SystemExit) as stopped:
        pilot_local.main()
    assert stopped.value.code == 1
    output = capsys.readouterr().out
    diagnostics = json.loads(output)['channels']
    assert 'Seed the selected' in diagnostics['assistant']['error']
    assert 'could not be read as JSON' in diagnostics['report']['error']
    assert 'fixture-secret' not in output


@pytest.mark.parametrize('situation', ('legacy_stale', 'stale', 'occupied', 'unknown_processes', 'live_worker', 'live_scheduler',
                                      'live_supervisor', 'marker_changed', 'foreign_instance'))
def test_stale_runtime_marker_requires_free_ports_and_known_processes(monkeypatch, configured_pilot, situation):
    pids = {'api': 11, 'web': 22, 'outbox': 33, 'reportWorker': 44}
    if situation != 'legacy_stale':
        pids.update(scheduler=55, supervisor=66)
    marker = {'web': pilot_local.WEB, 'api': pilot_local.API, 'pids': pids}
    if situation == 'foreign_instance':
        marker['web'] = 'http://127.0.0.1:32101'
    pilot_local.save('runtime.json', marker)
    pilot_local.local_file('stop.request').touch()
    bound = []

    @contextmanager
    def bind(port):
        if situation == 'occupied' and port == pilot_local.API_PORT:
            raise OSError('in use')
        bound.append(port)
        try:
            yield
        finally:
            bound.remove(port)

    def processes(recorded):
        assert recorded == pids
        assert bound == [pilot_local.WEB_PORT, pilot_local.API_PORT]
        if situation == 'live_worker':
            return {'reportWorker'}
        if situation == 'live_scheduler':
            return {'scheduler'}
        if situation == 'live_supervisor':
            return {'supervisor'}
        if situation == 'unknown_processes':
            return None
        if situation == 'marker_changed':
            pilot_local.save('runtime.json', marker | {'new': True})
        return set()

    monkeypatch.setattr(pilot_local, 'bind', bind)
    monkeypatch.setattr(pilot_local, 'probe_runtime_processes', processes)
    cleared = situation in ('legacy_stale', 'stale')
    assert pilot_local.clear_stale_runtime() == cleared
    assert pilot_local.local_file('runtime.json').is_file() != cleared
    assert pilot_local.local_file('stop.request').is_file() != cleared
    assert bound == []


def test_unresponsive_supervisor_retains_marker_without_pid_stop(monkeypatch, configured_pilot):
    pids = {'api': 11, 'web': 22, 'outbox': 33, 'scheduler': 44, 'supervisor': 55}
    runtime = {'web': pilot_local.WEB, 'api': pilot_local.API, 'pids': pids}
    pilot_local.save('runtime.json', runtime)
    monkeypatch.setattr(pilot_local, 'clear_stale_runtime', lambda: False)
    monkeypatch.setattr(pilot_local, 'wait_for', lambda check, seconds: check())
    monkeypatch.setattr(pilot_local.subprocess, 'run', lambda *args, **kwargs: pytest.fail('unexpected process operation'))
    with pytest.raises(pilot_local.PilotStopTimeout, match='Runtime marker retained'):
        pilot_local.stop()
    assert pilot_local.read('runtime.json') == runtime
    assert pilot_local.local_file('stop.request').is_file()


def test_saved_configuration_loads_only_into_the_right_process(monkeypatch, tmp_path):
    pilot_ids = {key: {'id': str(uuid4())} for key in pilot_local.PILOT_CODES}
    monkeypatch.setattr(pilot_local, 'HOME', tmp_path)
    monkeypatch.setattr(pilot_local, 'private_home', lambda: None)
    pilot_local.save('pilots.json', pilot_ids)
    answers = iter(('y', 'chat_completions_json', 'http://127.0.0.1:32101/chat', 'fixture-assistant', 'SIM-EXEC-001',
                    'y', 'pmo_json', 'http://127.0.0.1:32102/extract', 'fixture-reports', 'SIM-NEW-001'))
    keys = iter(('fixture-assistant-key', 'fixture-worker-key'))
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(pilot_local, 'getpass', lambda _: next(keys))
    pilot_local.configure_models()
    assert pilot_local.model_env(False)[2] == {}
    api, worker, summary = pilot_local.model_env(True)
    assert api['PMO_ASSISTANT_MODEL_TOKEN'] == 'fixture-assistant-key'
    assert worker['PMO_REPORT_MODEL_TOKEN'] == 'fixture-worker-key'
    assert 'PMO_REPORT_MODEL_TOKEN' not in api and 'PMO_ASSISTANT_MODEL_TOKEN' not in worker
    assert 'PMO_REPORT_MODEL_URL' not in api and 'PMO_ASSISTANT_MODEL_URL' not in worker
    assert api['PMO_REPORT_MODEL_PROJECTS'] == worker['PMO_REPORT_MODEL_PROJECTS'] == pilot_ids['SIM-NEW-001']['id']
    assert summary['assistant'] == {'version': 'fixture-assistant', 'protocol': 'chat_completions_json', 'projects': ['SIM-EXEC-001']}
    for k, v in api.items():
        monkeypatch.setenv(k, v)
    client = model_gateway.Gateway.for_previews()
    assert client is not None and client.protocol == 'chat_completions_json' and client.model == 'fixture-assistant'
    assert 'fixture-assistant-key' not in repr(client)
    monkeypatch.delenv('PMO_ASSISTANT_MODEL_VERSION')
    assert model_gateway.Gateway.for_previews() is None


def test_explicit_model_fixture_scopes_fail_closed(monkeypatch):
    project, drill, report = uuid4(), uuid4(), uuid4()
    digest = 'a' * 64
    monkeypatch.setenv('PMO_ASSISTANT_MODEL_PROJECTS', f'{project},{drill}')
    monkeypatch.setenv('PMO_ASSISTANT_CLOSING_ONLY_PROJECTS', str(drill))
    assert model_gateway.assistant_scope(project, 'ledger')
    assert model_gateway.assistant_scope(drill, 'closing')
    assert not model_gateway.assistant_scope(drill, 'ledger')

    monkeypatch.setenv('PMO_REPORT_MODEL_PROJECTS', str(project))
    monkeypatch.setenv('PMO_REPORT_MODEL_SOURCE', f'{report}:1:{digest}')
    assert model_gateway.report_scope(project, report, 1, digest)
    assert not model_gateway.report_scope(project, report, 2, digest)
    assert not model_gateway.report_scope(project, report, 1, 'b' * 64)
    assert not model_gateway.report_scope(project, uuid4(), 1, digest)
    assert not model_gateway.report_scope(drill, report, 1, digest)
    monkeypatch.setenv('PMO_REPORT_MODEL_SOURCE', f'{report}:1:malformed')
    assert not model_gateway.report_scope(project, report, 1, digest)


@pytest.mark.parametrize(('channel', 'enabled_flag', 'load'), (
    ('REPORT', 'PMO_REPORT_ANALYSIS', model_gateway.Gateway.from_env),
    ('ASSISTANT', 'PMO_ASSISTANT_PREVIEWS', model_gateway.Gateway.for_previews),
))
def test_env_loader_requires_explicit_project_uuids(monkeypatch, channel, enabled_flag, load):
    project, other = uuid4(), uuid4()
    prefix = f'PMO_{channel}_MODEL'
    monkeypatch.setenv(enabled_flag, '1')
    monkeypatch.setenv(f'{prefix}_URL', 'http://127.0.0.1:32101/model')
    monkeypatch.setenv(f'{prefix}_PROTOCOL', 'pmo_json')
    scope = f'{prefix}_PROJECTS'
    for invalid in (None, '', f'{project},not-a-uuid', f'{project},'):
        if invalid is None:
            monkeypatch.delenv(scope, raising=False)
        else:
            monkeypatch.setenv(scope, invalid)
        assert load() is None and not model_gateway.in_project_scope(channel, project)
    monkeypatch.setenv(scope, f'{project}, {other}')
    assert load() is not None and model_gateway.in_project_scope(channel, project)
    assert model_gateway.in_project_scope(channel, other)


def test_chat_mode_wraps_schema_and_nonexecutable_business_source():
    sample = {'excerpts': [{'sourceId': 's1', 'text': 'Synthetic authorized fragment only'}]}
    results = [{'reviewDraft': []}, {'lessonDrafts': []}, {'answer': []}, answer()]
    with gateway(*(completion(value) for value in results)) as (url, calls):
        configured = model_gateway.Gateway(httpx.URL(url), '', 'chat_completions_json', 'fixture-only-model')
        for (mode, schema, _, instruction), result in zip(PROFILES.values(), results):
            assert configured.generate(mode, instruction, sample, schema.model_json_schema(), retry=False) == result
        assert configured.candidates({'narrative': 'Work took 2.50 days.'}, {'type': 'object'}, retry=True) == results[-1]
    assert len(calls) == 4
    for call in calls:
        assert set(call) == {'model', 'messages', 'response_format'}
        assert call['model'] == 'fixture-only-model' and call['response_format'] == {'type': 'json_object'}
        assert [message['role'] for message in call['messages']] == ['system', 'user']
    assert json.loads(calls[0]['messages'][1]['content']) == sample
    assert 'failed validation' in calls[-1]['messages'][0]['content']


@pytest.mark.parametrize('payload', [completion({}, tool_calls=[{'name': 'not-authorized'}]),
    completion({}, content='{"x":1,"x":2}'), completion({}, content='{"x":NaN}'),
    completion({}, role='system'), completion({}, refusal='refused'), {'choices': []}])
def test_chat_rejects_ambiguous_or_action_bearing_responses(payload):
    with gateway(payload) as (url, calls):
        client = model_gateway.Gateway(httpx.URL(url), '', 'chat_completions_json', 'fixture')
        with pytest.raises(model_gateway.ModelFailure, match='MODEL_SCHEMA_INVALID'):
            client.candidates({}, {'type': 'object'}, retry=False)
    assert len(calls) == 1
