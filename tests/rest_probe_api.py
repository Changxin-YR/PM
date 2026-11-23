"""Test-only ASGI wrapper: replay already successful requests without credentials once per route."""
from pmo.api import app as product
from pmo.observability import emit
from scripts.rest_closeout_evidence import BOOTSTRAP, PROBES

checked: set[str] = set()
MAX_CAPTURE = 2 * 1024 * 1024


async def app(scope, receive, send):
    if scope['type'] != 'http':
        await product(scope, receive, send)
        return
    captured = bytearray()
    oversized = False
    status = None

    async def observe_request():
        nonlocal oversized
        message = await receive()
        body = message.get('body', b'')
        if not oversized and len(captured) + len(body) <= MAX_CAPTURE:
            captured.extend(body)
        else:
            oversized = True
        return message

    async def observe_response(message):
        nonlocal status
        if message['type'] == 'http.response.start':
            status = message['status']
        await send(message)

    await product(scope, observe_request, observe_response)
    name = getattr(scope.get('route'), 'operation_id', None)
    if name in PROBES and status == 200:
        emit('acceptance.rest_public', operationId=name, status=status)
    if not name or status is None or not 200 <= status < 300 or oversized or name in checked or name in BOOTSTRAP | PROBES:
        return
    checked.add(name)
    request = {k: scope[k] for k in ('type', 'asgi', 'http_version', 'method', 'scheme', 'path', 'raw_path',
               'query_string', 'root_path', 'client', 'server') if k in scope}
    request['headers'] = [(name, value) for name, value in scope['headers']
                          if name.lower() not in (b'authorization', b'cookie', b'x-csrf-token')]
    received = False
    status = None

    async def probe_request():
        nonlocal received
        if received:
            return {'type': 'http.disconnect'}
        received = True
        return {'type': 'http.request', 'body': bytes(captured), 'more_body': False}

    async def probe_response(message):
        nonlocal status
        if message['type'] == 'http.response.start':
            status = message['status']

    await product(request, probe_request, probe_response)
    emit('acceptance.rest_anonymous', operationId=name, status=status)
