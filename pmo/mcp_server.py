"""Stdio adapter. No database imports, no browser credentials, no arbitrary URLs."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
from uuid import UUID
from string import Formatter
from .change_contract import PROPOSAL, mcp_snapshots
from .tool_routes import ROUTES

import httpx
import jsonschema
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server('pmo-lite')
packaged_catalog = Path(__file__).with_name('mcp_catalog.json')
source_catalog = Path(__file__).resolve().parents[1] / 'docs/spec/11_MCP_工具契约.json'
catalog = json.loads((packaged_catalog if packaged_catalog.is_file() else source_catalog).read_text(encoding='utf-8'))
implemented = set(ROUTES)


def advertised(tool):
    name = tool['name']
    inputs, outputs = tool['inputSchema'], {**tool['outputSchema'], 'type': 'object'}
    if name in {'pmo_task_list', 'pmo_report_list'}:
        inputs = deepcopy(inputs)
        inputs['properties'].update(page={'type': 'integer', 'minimum': 1, 'default': 1},
                                    pageSize={'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20})
        if name == 'pmo_report_list':
            inputs['properties']['reportType'] = {'type': 'string', 'enum': ['daily', 'weekly']}
    if name in {'pmo_report_list', 'pmo_report_get'}:
        outputs = deepcopy(outputs)
        report = outputs['oneOf'][0]['properties']['data']
        if name == 'pmo_report_list':
            report = report['properties']['items']['items']
        report['properties']['isCurrentConfirmed'] = {'type': 'boolean'}
    if name in {'pmo_requirement_change_history', 'pmo_change_propose'}:
        outputs = deepcopy(outputs)
        record = outputs['oneOf'][0]['properties']['data']
        history = name == 'pmo_requirement_change_history'
        if history:
            record = record['properties']['items']['items']
        else:
            inputs = {**inputs, **PROPOSAL}
        record.update(mcp_snapshots(history=history))
    return types.Tool(name=name, description=tool['description'], inputSchema=inputs, outputSchema=outputs)


tools = {t['name']: advertised(t) for t in catalog['tools'] if t['name'] in implemented}

@server.list_tools()
async def discover():
    return list(tools.values())

def failed(code, operation=None):
    payload={'ok':False,'error':{'code':code,'message':code,'retryable':code=='SERVICE_UNAVAILABLE'},
             'meta':{'operationId':operation} if operation else {}}
    return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(payload))],structuredContent=payload,isError=True)


def api_endpoint(path: str) -> httpx.URL | None:
    try:
        raw = os.getenv('PMO_API_URL')
        if raw is None:
            port = int(os.getenv('PMO_API_PORT', '8000'))
            if not 1 <= port <= 65535:
                return None
            raw = f'http://127.0.0.1:{port}'
        url = httpx.URL(raw)
        loopback = url.host in ('127.0.0.1', '::1', 'localhost')
        if (url.scheme != 'https' and not (url.scheme == 'http' and loopback) or not url.host
                or (url.port is not None and not 1 <= url.port <= 65535)
                or url.userinfo or url.query or url.fragment or url.path not in ('/', '/api/v1', '/api/v1/')):
            return None
        return url.copy_with(path=f'/api/v1{path}')
    except (ValueError, httpx.InvalidURL):
        return None


@server.call_tool(validate_input=False)
async def invoke(name,arguments):
    if name not in tools: return failed('UNKNOWN_TOOL')
    try:
        jsonschema.Draft202012Validator(tools[name].inputSchema,format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER).validate(arguments)
    except jsonschema.ValidationError:
        return failed('VALIDATION_FAILED', name)
    method,template=ROUTES[name]
    body=dict(arguments)
    path=template.format(**{field:str(UUID(body.pop(field))) for _,field,_,_ in Formatter().parse(template) if field})
    url=api_endpoint(path)
    if url is None: return failed('PMO_API_CONFIGURATION_INVALID', name)
    token=os.environ.get('PMO_AGENT_TOKEN')
    if not token: return failed('UNAUTHORIZED', name)
    headers={'Authorization':f'Bearer {token}'}
    try:
        async with httpx.AsyncClient(timeout=10,trust_env=False,follow_redirects=False) as client:
            if method=='GET':
                filters=body.pop('filters',{})
                body.update({f'filters[{k}]':v for k,v in filters.items()})
                response=await client.get(url,headers=headers,params=body)
            else:
                headers['Idempotency-Key']=body.pop('idempotencyKey')
                if 'expectedRevision' in body: headers['If-Match']=str(body.pop('expectedRevision'))
                response=await client.request(method,url,headers=headers,json=body)
            if response.status_code>=500: return failed('SERVICE_UNAVAILABLE', name)
            if not response.is_success: return failed(response.json().get('code','VALIDATION_FAILED'), name)
            result={'ok':True,**response.json()}
            # REST editors need these fields; the V1 MCP output contract is closed.
            if name in {'pmo_requirement_change_history','pmo_change_propose'}:
                for record in result['data']['items'] if name=='pmo_requirement_change_history' else [result['data']]:
                    if record.pop('legacySnapshot', False):
                        record.pop('before', None)
                        record.pop('after', None)
                    for field in ('reason','requestedBy','baseRevision','revision','effectiveAt','decidedBy','decisionReason','decidedAt'): record.pop(field,None)
            if name in {'pmo_requirement_list','pmo_requirement_create','pmo_requirement_draft_update','pmo_requirement_deliver'}:
                records=result['data']['items'] if name=='pmo_requirement_list' else [result['data']]
                for record in records:
                    record.pop('acceptanceCriteria',None)
                    record.pop('plannedConfirmAt',None)
            if name in {'pmo_risk_list','pmo_risk_create','pmo_risk_update'}:
                records=result['data']['items'] if name=='pmo_risk_list' else [result['data']]
                for record in records:
                    for field in ('prob','impact','response','blockedSince'): record.pop(field,None)
            jsonschema.Draft202012Validator(tools[name].outputSchema).validate(result)
            return result
    except (httpx.HTTPError,ValueError,jsonschema.ValidationError):
        return failed('SERVICE_UNAVAILABLE', name)

async def serve():
    async with stdio_server() as (reader,writer):
        await server.run(reader,writer,server.create_initialization_options())


def main() -> None:
    asyncio.run(serve())


if __name__=='__main__': main()
