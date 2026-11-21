"""The same stdio test can start the installed launcher outside the repository."""
import asyncio
import os
import sys

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from pmo.mcp_server import api_endpoint, tools


@pytest.mark.parametrize('url', [
    'http://example.invalid', 'https://example.invalid/other/path', 'https://user:password@example.invalid',
    'https://example.invalid?next=unsafe', 'https://example.invalid/#fragment', 'https://', 'ftp://127.0.0.1',
    'http://127.0.0.1:bad', 'https://127.0.0.1:65536',
])
def test_invalid_endpoint_is_rejected_before_transport(monkeypatch, url):
    monkeypatch.setenv('PMO_API_URL', url)
    assert api_endpoint('/projects') is None


def test_tls_endpoint_and_legacy_port(monkeypatch):
    monkeypatch.setenv('PMO_API_URL', 'https://pmo.example.invalid/api/v1/')
    assert str(api_endpoint('/projects')) == 'https://pmo.example.invalid/api/v1/projects'
    monkeypatch.delenv('PMO_API_URL')
    monkeypatch.setenv('PMO_API_PORT', '9999')
    assert str(api_endpoint('/projects')) == 'http://127.0.0.1:9999/api/v1/projects'
    for bad in ('-1', '65536', 'not-a-port'):
        monkeypatch.setenv('PMO_API_PORT', bad)
        assert api_endpoint('/projects') is None


def test_configured_endpoint_and_wheel_stdio(live, actor, tmp_path):
    _, api, port, _ = live
    token, _ = actor()
    authorization = {'Authorization': f'Bearer {token}'}
    response = api.get('/api/v1/projects', headers=authorization)
    response.raise_for_status()
    expected = response.json()['data']
    launcher = os.getenv('PMO_TEST_MCP_ENTRYPOINT')

    async def call(url, secret):
        params = StdioServerParameters(command=launcher or sys.executable,
            args=[] if launcher else ['-m', 'pmo.mcp_server'], cwd=tmp_path if launcher else None,
            env={'PMO_API_URL': url, 'PMO_API_PORT': 'invalid', 'PMO_AGENT_TOKEN': secret, 'PYTHONUTF8': '1'})
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            published = (await session.list_tools()).tools
            assert {tool.name for tool in published} == set(tools) and len(published) == 40
            tool = next(tool for tool in published if tool.name == 'pmo_project_list')
            result = await session.call_tool(tool.name, {})
            payload = result.structuredContent
            jsonschema.Draft202012Validator(tool.outputSchema).validate(payload)
            assert payload['meta']['operationId'] == tool.name
            return result, payload

    endpoint = f'http://127.0.0.1:{port}/api/v1/'
    result, payload = asyncio.run(call(endpoint, token))
    assert not result.isError and payload['data'] == expected
    result, payload = asyncio.run(call('http://example.invalid/', token))
    assert result.isError and payload['error']['code'] == 'PMO_API_CONFIGURATION_INVALID' and token not in str(payload)
    result, payload = asyncio.run(call(endpoint, ''))
    assert result.isError and payload['error']['code'] == 'UNAUTHORIZED'
