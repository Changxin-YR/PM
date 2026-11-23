"""Issue independent synthetic delegations and hand them to a real stdio parent."""
import argparse
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import yaml

# Installed Host configurations always select the instance explicitly.
if '__main__' == __name__:
    if '--native' in sys.argv[1:]:
        os.environ['PMO_PILOT_RUNTIME'] = 'native'
    elif not __package__:
        os.environ.pop('PMO_PILOT_RUNTIME', None)

if __package__:
    from scripts import pilot_local as local
else:
    import pilot_local as local  # Absolute script path used by installed hosts.

HOSTS = ('codex', 'dsh')
ACTIONS = ['pmo_project_list', 'pmo_project_get', 'pmo_project_draft_update']


def issue(hosts):
    from scripts.pilot_seed import login

    local.private_home()
    pilot = local.read('pilots.json', {}).get('SIM-NEW-001')
    if not pilot or not local.ready():
        raise ValueError('Seed and start the dedicated pilot first.')
    pid = pilot['id']
    with ExitStack() as stack:
        client = login(stack, local.read('synthetic-logins.json'), 'init')
        project = client.get(f'projects/{pid}').raise_for_status().json()['data']['project']
        if project['code'] != 'SIM-NEW-001' or project['stage'] != 'drafting':
            raise ValueError('The synthetic delegated draft is no longer available.')
        for host in hosts:
            filename = f'host-{host}.json'
            previous = local.read(filename, {})
            state = client.get('me/agent-tokens?pageSize=100').raise_for_status().json()['data']
            match = next((item for item in state['items'] if item['tokenId'] == previous.get('tokenId')), None)
            if (match and match['state'] == 'active' and set(match['allowedActions']) == set(ACTIONS)
                    and match['allowedProjectIds'] == [pid] and not match['allowProjectCreate']
                    and datetime.fromisoformat(match['expiresAt']) > datetime.now(UTC) + timedelta(minutes=5)):
                granted = match
            else:
                if match and match['state'] == 'active':
                    client.post(f'me/agent-tokens/{match["tokenId"]}/revoke',
                        headers={'If-Match': str(match['revision']), 'Idempotency-Key': uuid4().hex},
                        json={'reason': 'Rotate this synthetic Host handoff.'}).raise_for_status()
                    state = client.get('me/agent-tokens').raise_for_status().json()['data']
                response = client.post('me/agent-tokens',
                    headers={'If-Match': str(state['collectionRevision']), 'Idempotency-Key': uuid4().hex},
                    json={'allowProjectCreate': False, 'allowedActions': ACTIONS, 'allowedProjectIds': [pid],
                          'expiresAt': (datetime.now(UTC) + timedelta(hours=1)).isoformat()}).raise_for_status()
                granted = response.json()['data']
                token = response.headers.get('X-PMO-New-Token', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
                    raise ValueError('Missing one-time token delivery; revoke the new receipt in the workbench.')
                local.save(filename, {**granted, 'token': token, 'host': host})
            print(json.dumps({k: granted[k] for k in ('tokenId', 'fingerprint', 'expiresAt')} | {'host': host}))


def stdio(host):
    config = local.read(f'host-{host}.json', {})
    token = config.get('token', '')
    if (config.get('host') != host or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token)
            or datetime.fromisoformat(config['expiresAt']) <= datetime.now(UTC)):
        raise ValueError('No live synthetic token for this Host. Use the local PMO issue command.')
    adapter = local.ROOT / '.venv' / 'Scripts' / 'pmo-mcp.exe'
    command = [str(adapter)] if adapter.is_file() else [str(local.PYTHON), '-m', 'pmo.mcp_server']
    env = local.env_base() | {'PMO_API_URL': local.API.rstrip('/'), 'PMO_AGENT_TOKEN': token}
    return subprocess.run(command, cwd=local.ROOT, env=env, check=False).returncode


def config():
    local.private_home()
    args = [str(Path(__file__).resolve()), 'stdio', 'codex', *(['--native'] if local.NATIVE else [])]
    line = f'[mcp_servers.pmo_synthetic_codex]\ncommand = {json.dumps(str(local.PYTHON))}\nargs = {json.dumps(args)}\n'
    local.local_file('codex-mcp.toml').write_text(line, encoding='utf-8')
    print(str(local.local_file('codex-mcp.toml')))
    dsh = [{'insert': [{'id': 'pmo-synthetic-mcp', 'name': '@deepseek-ai/dsh-mcp-client', 'config': {
        'serverName': 'pmo_synthetic_dsh', 'transport': 'stdio', 'command': str(local.PYTHON),
        'args': [args[0], 'stdio', 'dsh', *args[3:]], 'cwd': str(local.ROOT),
    }}]}]
    local.local_file('dsh-mcp.cordis.yml').write_text(yaml.safe_dump(dsh, sort_keys=False), encoding='utf-8')
    print(str(local.local_file('dsh-mcp.cordis.yml')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('issue', 'stdio', 'config'))
    parser.add_argument('host', choices=(*HOSTS, 'both'), nargs='?', default='both')
    parser.add_argument('--native', action='store_true', help='Use the separate native pilot instance.')
    args = parser.parse_args()
    if args.action == 'stdio':
        if args.host not in HOSTS:
            parser.error('Choose exactly one Host.')
        return stdio(args.host)
    if args.action == 'issue':
        issue(HOSTS if args.host == 'both' else (args.host,))
    else:
        config()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        # Never print response headers, token contents or SDK frames outside stdio.
        print(f'Synthetic Host handoff unavailable ({type(exc).__name__}).', file=sys.stderr)
        raise SystemExit(1) from None
