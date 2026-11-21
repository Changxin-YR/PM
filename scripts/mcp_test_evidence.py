"""Opt-in pytest evidence: actual stdio discovery and successful calls, never payloads or tokens."""
import json
from pathlib import Path

import jsonschema
from mcp import ClientSession
import pytest

from pmo.mcp_server import catalog, tools
from pmo.tool_routes import ROUTES

EXPECTED = {item['name'] for item in catalog['tools']}


class Evidence:
    def __init__(self):
        self.discovered = set()
        self.succeeded = {name: set() for name in EXPECTED}
        self.denied = {name: set() for name in EXPECTED}

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        call_tool, list_tools = ClientSession.call_tool, ClientSession.list_tools
        discovered, succeeded, denied = set(), set(), set()

        async def capture_list(session, *args, **kwargs):
            result = await list_tools(session, *args, **kwargs)
            for tool in result.tools:
                assert tool.inputSchema == tools[tool.name].inputSchema and tool.outputSchema == tools[tool.name].outputSchema
                discovered.add(tool.name)
            return result

        async def capture_call(session, name, *args, **kwargs):
            result = await call_tool(session, name, *args, **kwargs)
            payload = result.structuredContent
            if name in EXPECTED and payload is not None:
                jsonschema.Draft202012Validator(tools[name].outputSchema,
                    format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER).validate(payload)
                if not result.isError:
                    assert payload['ok'] and payload['meta']['operationId'] == name
                    succeeded.add(name)
                else:
                    assert not payload['ok']
                    denied.add((name, payload['error']['code']))
            return result

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(ClientSession, 'list_tools', capture_list)
            patch.setattr(ClientSession, 'call_tool', capture_call)
            outcome = yield
        self.discovered.update(discovered)
        for name in succeeded:
            self.succeeded[name].add(item.nodeid)
        for name, reason in denied:
            self.denied[name].add(reason)
        return outcome

    def pytest_sessionfinish(self, session, exitstatus):
        missing_calls = sorted(name for name in EXPECTED if not self.succeeded[name])
        missing_discovery = sorted(EXPECTED - self.discovered)
        status = 'PASS' if not exitstatus and not (missing_calls or missing_discovery) else 'FAIL'
        out = session.config.option.pmo_mcp_evidence
        Path(out).write_text(json.dumps({'status': status, 'registered': len(self.discovered),
            'successful': len(EXPECTED) - len(missing_calls), 'missingCalls': missing_calls,
            'missingDiscovery': missing_discovery, 'tools': {name: {
                'registered': name in self.discovered, 'successfulCaseIds': sorted(self.succeeded[name]),
                'observedErrorCodes': sorted(self.denied[name])} for name in sorted(EXPECTED)}}, indent=2) + '\n',
            encoding='utf-8')
        if status == 'FAIL' and exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_addoption(parser):
    parser.addoption('--pmo-mcp-evidence', metavar='PATH', help='Record each successful SDK tool call in a JSON file')


def pytest_configure(config):
    if config.option.pmo_mcp_evidence:
        assert EXPECTED == set(tools) == set(ROUTES) and len(EXPECTED) == 40
        config.pluginmanager.register(Evidence(), 'pmo-mcp-call-evidence')
