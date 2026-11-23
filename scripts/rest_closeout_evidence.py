"""Join live OpenAPI, actual endpoint bindings and synthetic HTTP observations; never export payloads."""
import argparse
from collections import Counter, defaultdict
import csv
import inspect
import json
from pathlib import Path

from fastapi.routing import APIRoute
import yaml

from pmo.api import app
from pmo.access import identity
from pmo.identity import HUMAN_ONLY_OPERATIONS
from pmo.tool_routes import ROUTES

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = {'auth_login', 'auth_accept_user_invitation'}
PROBES = {'ops_liveness', 'ops_readiness'}


def operations(doc):
    return {info['operationId']: (method.upper(), path, info)
            for path, item in doc['paths'].items() for method, info in item.items()
            if isinstance(info, dict) and 'operationId' in info}


def endpoints(router):
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield route
        elif original := getattr(route, 'original_router', None):
            yield from endpoints(original)


def bindings(observed):
    actual = operations(observed)
    expected = operations(yaml.safe_load(next((ROOT / 'docs/spec').glob('12_OpenAPI_*.yaml')).read_text(encoding='utf-8')))
    code = {route.operation_id: route for route in endpoints(app) if route.operation_id}
    assert len(expected) == 95 and len(ROUTES) == 40
    assert set(actual) == set(code)
    assert {k: v[:2] for k, v in actual.items()} == {k: v[:2] for k, v in operations(app.openapi()).items()}
    for name, (_, _, _) in expected.items():
        assert actual[name][:2] == expected[name][:2], name
    for name, (method, path, _) in actual.items():
        route = code[name]
        assert route.path == path and method in route.methods, name
        if name in ROUTES:
            assert (method, path) == (ROUTES[name][0], '/api/v1' + ROUTES[name][1]), name
        if name not in BOOTSTRAP | PROBES:
            assert any(dep.call is identity for dep in route.dependant.dependencies), name
            assert name in ROUTES or name in HUMAN_ONLY_OPERATIONS, name
        function = route.endpoint
        yield {'operation': name, 'method': method, 'path': path,
               'catalog': 'baseline' if name in expected else 'engineering',
               'actor_boundary': ('public probe' if name in PROBES else 'human bootstrap' if name in BOOTSTRAP else
                                  'identity + dual-entry action' if name in ROUTES else 'identity + human only'),
               'handler': Path(inspect.getfile(function)).relative_to(ROOT).as_posix() + ':' + function.__name__,
               'stdio': name if name in ROUTES else ''}


def observations(paths):
    found = defaultdict(Counter)
    probes = defaultdict(Counter)
    for path in paths:
        with path.open(encoding='utf-8-sig') as stream:
            for line in stream:
                offset = line.find('{"event":')
                if offset < 0:
                    continue
                try:
                    event, _ = json.JSONDecoder().raw_decode(line[offset:])
                except json.JSONDecodeError:
                    continue
                if event['event'] in ('http.request', 'acceptance.rest_public'):
                    found[event['operationId']][event['status']] += 1
                elif event['event'] == 'acceptance.rest_anonymous':
                    probes[event['operationId']][event['status']] += 1
    return found, probes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('openapi', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('logs', type=Path, nargs='*')
    parser.add_argument('--require-complete', action='store_true', help='Require a real success and applicable anonymous probe per route.')
    args = parser.parse_args()
    found, probes = observations(args.logs)
    rollup = Counter()
    rows = []
    for item in bindings(json.loads(args.openapi.read_text(encoding='utf-8-sig'))):
        name = item['operation']
        seen = found[name]
        failed_probes = set(probes[name]) - {401}
        assert not failed_probes, (name, failed_probes)
        success = sum(value for status, value in seen.items() if 200 <= status < 300)
        if args.require_complete:
            assert success, (name, 'missing successful request')
            assert name in BOOTSTRAP | PROBES or probes[name][401], (name, 'missing anonymous probe')
        denied = sum(seen[s] for s in (401, 403, 404))
        rollup['success_observed'] += bool(success)
        rollup['boundary_status_observed'] += bool(denied)
        rows.append(item | {'successes_in_logs': success, 'same_request_without_identity': probes[name][401],
                            '401': seen[401], '403': seen[403], '404': seen[404],
                            '409': seen[409], '422': seen[422], '5xx': sum(n for s, n in seen.items() if s >= 500)})
    with args.output.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (row['catalog'], row['path'], row['method'])))
    print(dict(Counter(row['catalog'] for row in rows)), dict(rollup))


if __name__ == '__main__':
    main()
