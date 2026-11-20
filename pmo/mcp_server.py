"""Stdio adapter. No database imports, no browser credentials, no arbitrary URLs."""
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID
from string import Formatter
from .tool_routes import ROUTES

import httpx
import jsonschema
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server('pmo-lite')
catalog = json.loads(next((Path(__file__).resolve().parents[1]/'docs/spec').glob('11_*.json')).read_text(encoding='utf-8'))
implemented = set(ROUTES)
tools = {t['name']: types.Tool(name=t['name'],description=t['description'],inputSchema=t['inputSchema'],outputSchema={**t['outputSchema'],'type':'object'}) for t in catalog['tools'] if t['name'] in implemented}

@server.list_tools()
async def discover():
    return list(tools.values())

def failed(code):
    payload={'ok':False,'error':{'code':code,'message':code,'retryable':code=='SERVICE_UNAVAILABLE'},'meta':{}}
    return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(payload))],structuredContent=payload,isError=True)

@server.call_tool(validate_input=False)
async def invoke(name,arguments):
    if name not in tools: return failed('UNKNOWN_TOOL')
    try:
        jsonschema.Draft202012Validator(tools[name].inputSchema,format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER).validate(arguments)
    except jsonschema.ValidationError:
        return failed('VALIDATION_FAILED')
    port=int(os.environ.get('PMO_API_PORT','8000'))
    method,template=ROUTES[name]
    body=dict(arguments)
    path=template.format(**{field:str(UUID(body.pop(field))) for _,field,_,_ in Formatter().parse(template) if field})
    url=f'http://127.0.0.1:{port}/api/v1{path}'
    token=os.environ.get('PMO_AGENT_TOKEN')
    if not token: return failed('UNAUTHORIZED')
    headers={'Authorization':f'Bearer {token}'}
    try:
        async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
            if method=='GET':
                filters=body.pop('filters',{})
                body.update({f'filters[{k}]':v for k,v in filters.items()})
                response=await client.get(url,headers=headers,params=body)
            else:
                headers['Idempotency-Key']=body.pop('idempotencyKey')
                if 'expectedRevision' in body: headers['If-Match']=str(body.pop('expectedRevision'))
                response=await client.request(method,url,headers=headers,json=body)
            if response.status_code>=500: return failed('SERVICE_UNAVAILABLE')
            if not response.is_success: return failed(response.json().get('code','VALIDATION_FAILED'))
            result={'ok':True,**response.json()}
            # REST editors need these fields; the V1 MCP output contract is closed.
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
        return failed('SERVICE_UNAVAILABLE')

async def main():
    async with stdio_server() as (reader,writer):
        await server.run(reader,writer,server.create_initialization_options())

if __name__=='__main__': asyncio.run(main())
