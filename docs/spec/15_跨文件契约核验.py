#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PMO standalone engineering-contract cross-check.

Validates technical specifications, not deployed code. Fail closed on manifest and
OpenAPI drift; explicitly report unimplemented M0 work instead of passing it.
"""
import csv, hashlib, json, re, runpy, sys
from collections import Counter
from pathlib import Path

import jsonschema
import yaml

D=Path(__file__).resolve().parent
SRC_HASH='e88d37a5e59fe2e3c14b768ab9d11ae11100b68308278c9bf9b5a0ab1c68a532'
SPEC='PMO-Standalone-1.0.4'

def fail(msg):raise AssertionError(msg)
def require(b,msg):
    if not b:fail(msg)
def load(name):return (D/name).read_text('utf-8-sig')
def rows(name):return list(csv.DictReader((D/name).open('r',encoding='utf-8-sig',newline='')))


def main():
    require(hashlib.sha256((D/'原始业务方案_PMOLite.html').read_bytes()).hexdigest()==SRC_HASH,'HTML source changed')
    y=yaml.safe_load(load('02_PMO_V1_业务规则基线.yaml'))
    a=yaml.safe_load(load('12_OpenAPI_路由请求基线.yaml'))
    m=json.loads(load('11_MCP_工具契约.json'))
    specs=load('01_PMO_V1_独立系统完整开发方案.md')
    api_doc=load('12_API_工程接口契约.md')
    require(y['spec_version']==SPEC and m['packageSpecVersion']==SPEC and a['info']['version']=='1.0.4','versions drift')
    require(y['rule_version']==m['ruleVersion']=='lite-v1.0-standalone.3','policy version drift after engineering-only edit')
    require(SPEC in specs, 'main spec version not updated')
    require(specs.count('## 8. MCP 工具、Agent 四模式与证据处理')==1,'duplicate section 8')
    require(len(y['states'])==13 and len(y['rule_events'])==8 and len(y['agent']['modes'])==4,'business invariants changed')
    require(y['scorecards']['closing']['conditional_close']['exception_scope']=='requirements_only','closing exception too broad')
    require(y['money_effort']['reversal']['no_double_subtraction'],'cost double subtraction regression')
    require(y['mcp']['org_scoped_project_draft_create']['target_org_derived_from_verified_token_not_request'],'project create scope unsafe')

    # Both OpenAPI schema internals and security markers are checked independently.
    operations={}; paths={}; human=[]; partial_response=[]
    for route,methods in a['paths'].items():
        require(route.startswith('/api/v1/'),f'non-versioned route {route}')
        for verb,op in methods.items():
            require(verb in {'get','post','put','patch','delete'},f'invalid verb {verb}')
            oid=op['operationId']
            require(oid not in operations,f'duplicate operationId {oid}')
            operations[oid]=(verb.upper(),route,op)
            paths[(verb.upper(),route)]=oid
            for par in re.findall(r'\{([^}]+)\}',route):
                require(any(p.get('in')=='path' and p.get('name')==par and p.get('required') for p in op.get('parameters',[])),f'{oid} missing {par}')
            for p in op.get('parameters',[]):
                jsonschema.Draft202012Validator.check_schema(p['schema'])
                if p['in']=='query' and p['schema'].get('type')=='object':
                    require(p.get('style')=='deepObject' and p.get('explode') is True,f'{oid} nested object query is ambiguous')
            for ctype,content in op.get('requestBody',{}).get('content',{}).items():
                jsonschema.Draft202012Validator.check_schema(content['schema'])
                if ctype=='application/json' and content['schema'].get('type')=='object':
                    require(content['schema'].get('additionalProperties') is False,f'{oid} accepts unspecified input fields')
            if verb in ('post','put','patch','delete') and oid not in ('auth_login','auth_logout'):
                require(any(p.get('in')=='header' and p.get('name')=='Idempotency-Key' and p.get('required') for p in op.get('parameters',[])),f'{oid} missing idempotency key')
            is_human=op.get('x-forbidAgentEvenViaHttp',False)
            if is_human:
                human.append(oid)
                require(op['x-actorTypes']==['human'],f'{oid} agent allowed in human-only operation')
                require(op.get('security')==[{'SessionCookie':[]}],f'{oid} exposes bearer auth')
            elif op.get('x-actorTypes')==['human'] and oid!='auth_login':
                require(op.get('security')==[{'SessionCookie':[]}],f'{oid} human route has wrong security')
            for status,r in op.get('responses',{}).items():
                for typ,content in r.get('content',{}).items():
                    sch=content.get('schema',{})
                    if '$ref' in sch:
                        target=sch['$ref'].split('/')[-1]
                        require(target in a['components']['schemas'],f'{oid} unresolved response schema {target}')
            if oid!='auth_login' and not (op.get('security') or a.get('security')):
                fail(f'{oid} unauthenticated endpoint')
    require(len(operations)>=90 and len(human)>=30,f'API operations not complete: {len(operations)}, human-only: {len(human)}')

    # All declared MCP tools MUST map to precisely one allowed API operation.
    tools=m['tools'];require(len(tools)==40,'MCP count drift without reviewed change')
    names=set(); writers=0; output_incomplete=0
    for t in tools:
        nm=t['name'];require(nm not in names,f'duplicate MCP {nm}');names.add(nm)
        require(nm in operations,f'MCP without HTTP implementation contract: {nm}')
        require('agent' in operations[nm][2]['x-actorTypes'],f'MCP tries to call human-only: {nm}')
        jsonschema.Draft202012Validator.check_schema(t['inputSchema'])
        jsonschema.Draft202012Validator.check_schema(t['outputSchema'])
        require(t['inputSchema'].get('type')=='object' and t['inputSchema'].get('additionalProperties') is False,f'unsafe tool input: {nm}')
        if t['kind'] not in ('read','derived_read'):
            writers+=1
            require('idempotencyKey' in t['inputSchema'].get('required',[]),f'missing idempotency in MCP {nm}')
        if t.get('outputStatus')=='typed_business_data_design__M0_validate_in_sdk':
            data_schema=next(x['properties']['data'] for x in t['outputSchema']['oneOf'] if x.get('properties',{}).get('ok',{}).get('const') is True)
            require(data_schema.get('type')=='object' and bool(data_schema.get('required')) and data_schema.get('additionalProperties') is False,f'{nm}: data output contract is unconstrained')
            output_incomplete+=1
    require('pmo_project_create' in names and 'pmo_project_create' not in human,'project create accidentally gated as human-only')
    pc=next(t for t in tools if t['name']=='pmo_project_create')
    require(pc.get('requiresOrganizationCreateGrant') is True,'MCP project create grant missing')
    createop=operations['pmo_project_create'][2]
    require('delegated_organization' in createop.get('x-scope',''),'Agent project-create API scope ambiguous')
    req_schema=operations['human_agent_token_create'][2]['requestBody']['content']['application/json']['schema']
    require(req_schema['properties']['allowedProjectIds']['minItems']==0 and 'allowProjectCreate' in req_schema['properties'],'token create cannot support new projects')
    require(output_incomplete==40,'typed MCP response DTO coverage incomplete')

    required_ops=['human_charter_get','human_stakeholder_list','human_cost_postings_list',
                  'human_my_role_invitations','human_milestone_complete','human_task_create',
                  'human_report_submit_existing','human_report_analyze','admin_users_list','admin_user_invite',
                  'admin_user_update_status','admin_agent_tokens_list','admin_agent_token_revoke','admin_members_list',
                  'human_review_score','human_customer_confirm','human_evidence_upload',
                  'human_review_decide','human_verify_cost_posting','human_change_approve']
    for oid in required_ops:require(oid in operations,f'Web flow operation omitted: {oid}')
    evidence=operations['human_evidence_upload'][2]['requestBody']['content']
    require(set(evidence)=={'multipart/form-data'},'file evidence must send real bytes')
    es=evidence['multipart/form-data']['schema'];require('file' in es['required'] and es['properties']['file'].get('format')=='binary','evidence bytes missing')
    require('sha256' not in es['properties'],'user-provided sha256 falsely trusted')
    score=operations['human_review_score'][2]['requestBody']['content']['application/json']['schema']
    for part in score['properties']['dimensions']['oneOf']:
        require(all('score' in v['required'] and 'selectedAnchorCode' in v['required'] and 'rationale' in v['required'] for v in part['properties'].values()),'scorecard loses evidence/anchor')
    require('/api/v1/projects/{projectId}/transitions/preview' not in a['paths'],'obsolete route mixed into openapi')

    # Machine-readable dictionary coverage, not executable migration proof.
    rr=rows('14_数据库字段类型与约束矩阵.csv')
    business=sorted({x['table'] for x in rr if x['group']=='BUSINESS'})
    tech=sorted({x['table'] for x in rr if x['group']=='TECHNICAL'})
    require(len(business)==16,f'business class tables mismatch: {business}')
    require(len({(x['table'],x['field']) for x in rr})==len(rr),'duplicate database column declaration')
    lookup={(x['table'],x['field']) for x in rr}
    for table in business:
        for field in ('id','created_at','updated_at','created_by','revision'):
            require((table,field) in lookup,f'{table}.{field} missing in DB dictionary')
    for field in ('org_id','allow_project_create'):
        require(('agent_credentials',field) in lookup,f'agent credential {field} missing')
    require('multipart/form-data' in api_doc,'Web contract does not mention verified file uploads')

    # Published test vector oracle checks do not compile/execute application code.
    reference=runpy.run_path(str(D/'08_静态核验脚本.py'))
    reference['main']()
    print('\nV1.0.4 CROSS-FILE CONTRACT CHECK PASS')
    print(f'  original source hash: {SRC_HASH}')
    print(f'  spec={SPEC}, ruleVersion={y["rule_version"]}')
    print(f'  API operations={len(operations)}, paths={len(a["paths"])}, human-only={len(human)}')
    print(f'  MCP tools={len(tools)}, writes={writers}, typed output data contracts (runtime SDK verification pending)={output_incomplete}')
    print(f'  DB dictionary business tables={len(business)}, technical tables={len(tech)}, field declarations={len(rr)}')
    print('  UNSATISFIED BY DESIGN: real migrations / API server / SDK registration / Web E2E / host compatibility / business approvals')

if __name__=='__main__':main()
