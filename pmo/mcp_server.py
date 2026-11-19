"""Stdio adapter. No database imports, no browser credentials, no arbitrary URLs."""
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import jsonschema
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server('pmo-lite')
catalog = json.loads(next((Path(__file__).resolve().parents[1]/'docs/spec').glob('11_*.json')).read_text(encoding='utf-8'))
implemented = {'pmo_project_list','pmo_project_create','pmo_project_get','pmo_project_draft_update'}
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
    url=f'http://127.0.0.1:{port}/api/v1/projects'
    token=os.environ.get('PMO_AGENT_TOKEN')
    if not token: return failed('UNAUTHORIZED')
    headers={'Authorization':f'Bearer {token}'}
    try:
        async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
            if name=='pmo_project_create':
                headers['Idempotency-Key']=arguments['idempotencyKey']
                response=await client.post(url,headers=headers,json={k:v for k,v in arguments.items() if k!='idempotencyKey'})
            elif name=='pmo_project_get':
                response=await client.get(f"{url}/{UUID(arguments['projectId'])}",headers=headers)
            elif name=='pmo_project_draft_update':
                headers.update({'If-Match':str(arguments['expectedRevision']),'Idempotency-Key':arguments['idempotencyKey']})
                response=await client.patch(f"{url}/{UUID(arguments['projectId'])}",headers=headers,json={'fields':arguments['fields']})
            else:
                query={k:arguments[k] for k in ('page','pageSize') if k in arguments}
                query.update({f'filters[{k}]':v for k,v in arguments.get('filters',{}).items()})
                response=await client.get(url,headers=headers,params=query)
            if response.status_code>=500: return failed('SERVICE_UNAVAILABLE')
            if not response.is_success: return failed(response.json().get('code','VALIDATION_FAILED'))
            result={'ok':True,**response.json()}
            jsonschema.Draft202012Validator(tools[name].outputSchema).validate(result)
            return result
    except (httpx.HTTPError,ValueError,jsonschema.ValidationError):
        return failed('SERVICE_UNAVAILABLE')

async def main():
    async with stdio_server() as (reader,writer):
        await server.run(reader,writer,server.create_initialization_options())

if __name__=='__main__': asyncio.run(main())
