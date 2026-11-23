"""Exercise a disposable loopback ingress with a synthetic administrator."""
import argparse
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from secrets import token_urlsafe
import ssl
from struct import pack
from uuid import uuid4
from zlib import crc32

import httpx
from PIL import Image


FILE_LIMIT = 20 * 1024 * 1024
ADMIN = 'synthetic-ingress-admin'


def png_at_limit():
    stream = BytesIO()
    Image.new('RGB', (1, 1)).save(stream, format='PNG')
    png = stream.getvalue()
    padding = b'pADd' + bytes(FILE_LIMIT - len(png) - 12)
    chunk = pack('>I', len(padding) - 4) + padding + pack('>I', crc32(padding))
    return png[:-12] + chunk + png[-12:]


def write_headers(csrf='', revision=None):
    headers = {'Idempotency-Key': uuid4().hex}
    if csrf:
        headers['X-CSRF-Token'] = csrf
    if revision is not None:
        headers['If-Match'] = str(revision)
    return headers


def check_invitation(client, connect, csrf):
    tag = uuid4().hex[:8]
    issued = client.post('admin/users/invitations', headers=write_headers(csrf),
                         json={'email': f'tls-{tag}@example.test', 'role': 'member'}).raise_for_status()
    code = issued.headers['X-PMO-Org-Invitation']
    profile = {'username': f'tls-{tag}', 'password': token_urlsafe(24)}
    with connect() as invitee:
        accepted = invitee.post('auth/invitations/accept', headers=write_headers(revision=1),
                               json={'invitationCode': code, 'displayName': 'Synthetic invitee', **profile})
        assert accepted.raise_for_status().json()['data']['isSystemAdmin'] is False
        assert invitee.post('auth/login', json=profile).status_code == 200


def check_evidence(client, csrf, project):
    project_id = project['projectId']
    milestone = client.post(f'projects/{project_id}/milestones', headers=write_headers(csrf),
                            json={'name': 'Synthetic evidence subject', 'plannedDate': '2027-01-10'})
    link = {'linkedResourceType': 'milestone',
            'linkedResourceId': milestone.raise_for_status().json()['data']['milestoneId']}
    path = f'projects/{project_id}/evidence'
    png = png_at_limit()

    def upload(contents, revision):
        return client.post(path, headers=write_headers(csrf, revision), data=link,
                           files={'file': ('synthetic.png', contents, 'image/png')})

    stored = upload(png, 1).raise_for_status().json()['data']
    assert stored['sizeBytes'] == FILE_LIMIT and stored['sha256'] == sha256(png).hexdigest()
    over_file_limit = upload(png + b'x', 2)
    assert over_file_limit.status_code == 413 and over_file_limit.json()['code'] == 'INVALID_EVIDENCE_SIZE'
    over_request_limit = upload(png + bytes(65536), 2)
    assert over_request_limit.status_code == 413 and over_request_limit.headers['content-type'].startswith('text/html')
    other_route = client.post('projects', headers=write_headers(csrf), content=bytes(16 * 1024 * 1024 + 1))
    assert other_route.status_code == 413 and other_route.headers['content-type'].startswith('text/html')
    assert client.get(path).raise_for_status().json()['data']['total'] == 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', type=httpx.URL)
    parser.add_argument('--ca-file', type=Path)
    args = parser.parse_args()
    url = args.url
    if url.host != '127.0.0.1' or url.scheme not in ('http', 'https') or url.path != '/':
        parser.error('Use a dedicated disposable 127.0.0.1 test ingress')
    https = url.scheme == 'https'
    if https != (args.ca_file is not None):
        parser.error('Supply the test CA file for HTTPS only')
    context = ssl.create_default_context(cafile=str(args.ca_file)) if https else True
    assert __debug__, 'Run with assertions enabled'

    def connect():
        return httpx.Client(base_url=url.join('/api/v1/'), verify=context, trust_env=False, timeout=45)

    with connect() as client:
        assert client.get(url.join('/health/ready')).json() == {'status': 'ready'}
        forged = {'X-Forwarded-Proto': 'http', 'X-Forwarded-For': '192.0.2.5',
                  'Forwarded': 'for=192.0.2.5;proto=http'}
        login = client.post('auth/login', headers=forged,
                            json={'username': ADMIN, 'password': os.environ['PMO_INGRESS_SMOKE_PASSWORD']})
        csrf = login.raise_for_status().json()['csrfToken']
        cookie = login.headers['set-cookie'].lower()
        assert ('; secure' in cookie) == https
        assert '; httponly' in cookie and '; samesite=strict' in cookie
        project = {'code': f'INGRESS-{uuid4().hex[:8]}', 'name': 'Synthetic ingress evidence test'}
        rejected = client.post('projects', headers=write_headers(), json=project)
        assert rejected.status_code == 403 and rejected.json()['code'] == 'CSRF_INVALID'
        created = client.post('projects', headers=write_headers(csrf), json=project).raise_for_status().json()['data']
        check_invitation(client, connect, csrf)
        print(f'{url.scheme}: login and invitation accepted, cookie attributes checked, missing CSRF denied')
        check_evidence(client, csrf, created)
        print(f'{url.scheme}: {FILE_LIMIT} byte PNG accepted; API file and proxy request limits enforced')


if __name__ == '__main__':
    main()
