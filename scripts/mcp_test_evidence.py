"""Opt-in pytest evidence for stdio discovery, calls and committed write replays."""
import json
from pathlib import Path

import jsonschema
from mcp import ClientSession
import pytest
import sqlalchemy as sa

from pmo import schema_v1 as db
from pmo.audit_store import audit, stable_refs
from pmo.mcp_server import catalog, tools
from pmo.projects import fingerprint
from pmo.tool_routes import ROUTES

EXPECTED = {item['name'] for item in catalog['tools']}
WRITES = {name for name, (method, _) in ROUTES.items() if method != 'GET'}
READS = EXPECTED - WRITES


def write_receipt(engine, name, args, payload):
    """Identify the first committed response, even if another actor reused this key."""
    key = args['idempotencyKey']
    i, a, e = db.idempotency_keys.c, db.audit_logs.c, db.outbox_events.c
    with engine.connect() as connection:
        rows = connection.execute(sa.select(db.idempotency_keys).where(i.actor_type == 'agent',
            i.operation == name, i.idempotency_key == key)).mappings()
        receipt, = [row for row in rows if row.response_body and row.response_body['meta'] == payload['meta']]
        assert receipt.status == 'completed' and receipt.response_status == 200
        assert receipt.response_hash == fingerprint(receipt.response_body)
        audit = connection.execute(sa.select(a.request_id, a.project_id, a.delegated_by, a.after_hash).where(
            a.actor_type == 'agent', a.actor_id == receipt.actor_id, a.org_id == receipt.org_id,
            a.action == name, a.idempotency_key == key, a.result == 'success')).one()
        event = connection.execute(sa.select(e.id, e.payload).where(e.dedupe_key == audit.request_id)).one()
        assert audit.delegated_by and audit.after_hash == receipt.response_hash
        assert audit.project_id and event.payload['projectId'] == str(audit.project_id)
        return receipt.id, audit.request_id, event.id


def read_receipt(engine, name, since, payload):
    log = audit.c
    path = '/api/v1' + ROUTES[name][1]
    with engine.connect() as connection:
        row = connection.execute(sa.select(audit).where(log.action == name, log.actor_type == 'agent',
            log.result == 'success', log.created_at >= since)).mappings().one()
        assert row.delegated_by and row.request_path == path and row.org_id and row.after_hash
        assert bool(row.project_id) == ('{' in path) and row.idempotency_key is None
        assert row.source_refs == stable_refs(payload['meta']['sourceRefs']) and row.rule_version == payload['meta']['ruleVersion']
        assert connection.scalar(sa.select(db.outbox_events.c.id).where(db.outbox_events.c.dedupe_key == row.request_id)) is None


class Evidence:
    def __init__(self):
        self.discovered = set()
        self.succeeded = {name: set() for name in EXPECTED}
        self.denied = {name: set() for name in EXPECTED}
        self.replayed = {name: set() for name in WRITES}
        self.read_audited = {name: set() for name in READS}

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        call_tool, list_tools = ClientSession.call_tool, ClientSession.list_tools
        discovered, succeeded, denied, replayed, read_audited = set(), set(), set(), set(), set()
        verify_replays = item.config.option.pmo_mcp_check_replays

        async def capture_list(session, *args, **kwargs):
            result = await list_tools(session, *args, **kwargs)
            for tool in result.tools:
                assert tool.inputSchema == tools[tool.name].inputSchema and tool.outputSchema == tools[tool.name].outputSchema
                discovered.add(tool.name)
            return result

        async def capture_call(session, name, *args, **kwargs):
            since = None
            if verify_replays and name in READS and not self.read_audited[name] and name not in read_audited:
                with item.funcargs['live_stack'][0].connect() as connection:
                    since = connection.scalar(sa.select(sa.func.clock_timestamp()))
            result = await call_tool(session, name, *args, **kwargs)
            payload = result.structuredContent
            if name in EXPECTED and payload is not None:
                jsonschema.Draft202012Validator(tools[name].outputSchema,
                    format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER).validate(payload)
                if not result.isError:
                    assert payload['ok'] and payload['meta']['operationId'] == name
                    succeeded.add(name)
                    if since is not None:
                        read_receipt(item.funcargs['live_stack'][0], name, since, payload)
                        read_audited.add(name)
                    if verify_replays and name in WRITES and not self.replayed[name] and name not in replayed:
                        arguments = args[0] if args else kwargs['arguments']
                        engine, *_ = item.funcargs['live_stack']
                        receipt = write_receipt(engine, name, arguments, payload)
                        repeated = await call_tool(session, name, *args, **kwargs)
                        assert not repeated.isError and repeated.structuredContent == payload, name
                        assert write_receipt(engine, name, arguments, payload) == receipt, name
                        replayed.add(name)
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
        for name in replayed:
            self.replayed[name].add(item.nodeid)
        for name in read_audited:
            self.read_audited[name].add(item.nodeid)
        return outcome

    def pytest_sessionfinish(self, session, exitstatus):
        missing_calls = sorted(name for name in EXPECTED if not self.succeeded[name])
        missing_discovery = sorted(EXPECTED - self.discovered)
        check_replays = session.config.option.pmo_mcp_check_replays
        missing_replays = sorted(name for name in WRITES if not self.replayed[name]) if check_replays else []
        missing_read_audits = sorted(name for name in READS if not self.read_audited[name]) if check_replays else []
        missing_action_denials = sorted(name for name in EXPECTED if 'FORBIDDEN' not in self.denied[name]) if check_replays else []
        status = 'FAIL' if (exitstatus or missing_calls or missing_discovery or missing_replays
                            or missing_action_denials or missing_read_audits) else 'PASS'
        out = session.config.option.pmo_mcp_evidence
        Path(out).write_text(json.dumps({'status': status, 'registered': len(self.discovered),
            'successful': len(EXPECTED) - len(missing_calls), 'missingCalls': missing_calls,
            'missingDiscovery': missing_discovery, 'missingWriteReplays': missing_replays,
            'missingReadAudits': missing_read_audits,
            'missingForbidden': missing_action_denials, 'tools': {name: {
                'registered': name in self.discovered, 'successfulCaseIds': sorted(self.succeeded[name]),
                'writeReplayCaseIds': sorted(self.replayed.get(name, ())),
                'readAuditCaseIds': sorted(self.read_audited.get(name, ())),
                'observedErrorCodes': sorted(self.denied[name])} for name in sorted(EXPECTED)}}, indent=2) + '\n',
            encoding='utf-8')
        if status == 'FAIL' and exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_addoption(parser):
    parser.addoption('--pmo-mcp-evidence', metavar='PATH', help='Record each successful SDK tool call in a JSON file')
    parser.addoption('--pmo-mcp-check-replays', action='store_true',
                     help='Also replay one write per tool and require FORBIDDEN for every tool')


def pytest_configure(config):
    if config.option.pmo_mcp_check_replays and not config.option.pmo_mcp_evidence:
        raise pytest.UsageError('--pmo-mcp-check-replays requires --pmo-mcp-evidence')
    if config.option.pmo_mcp_evidence:
        assert EXPECTED == set(tools) == set(ROUTES) and len(EXPECTED) == 40
        config.pluginmanager.register(Evidence(), 'pmo-mcp-call-evidence')
