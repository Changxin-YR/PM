"""Verify the opt-in JSON chat compatibility contract and protected configuration split."""
import json
from uuid import uuid4

import httpx
import pytest

from pmo import model_gateway
from pmo.assistant_model import PROFILES
from scripts import pilot_local
from test_report_analysis import answer, gateway


def completion(value, **updates):
    return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(value), **updates}}]}


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
