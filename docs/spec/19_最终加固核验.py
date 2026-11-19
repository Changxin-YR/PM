#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V1.0.4 final specification hardening check.

Only validates source retention, declared schema consistency, and minimal schema
strength; does NOT execute real PMO application code or claim production PASS.
"""
from pathlib import Path
import csv, hashlib, json, re
import jsonschema, yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT=Path(__file__).resolve().parent
SOURCE_HASH='e88d37a5e59fe2e3c14b768ab9d11ae11100b68308278c9bf9b5a0ab1c68a532'
SPEC='PMO-Standalone-1.0.4'

def req(b,msg):
    if not b: raise AssertionError(msg)

def load(name):return (ROOT/name).read_text('utf-8-sig')

def sample(schema):
    if '$ref' in schema:raise AssertionError('Unresolved ref in generated test')
    if 'const' in schema:return schema['const']
    if 'enum' in schema:return schema['enum'][0]
    if 'oneOf' in schema:
        return sample(schema['oneOf'][0])
    t=schema.get('type')
    if t=='object':return {k:sample(schema['properties'][k]) for k in schema.get('required',[])}
    if t=='array':return []
    if t=='string':
        if schema.get('format')=='uuid':return '1ac6f4ef-26c2-4944-9d53-a54b48ad594d'
        if schema.get('format')=='date-time':return '2026-11-19T00:00:00Z'
        if schema.get('format')=='date':return '2026-11-19'
        if schema.get('pattern'):
            return '0.00'
        return 'test'
    if t=='integer':return max(1,schema.get('minimum',0))
    if t=='number':return max(1,schema.get('minimum',0))
    if t=='boolean':return False
    raise AssertionError(f'Cannot sample {schema}')

def main():
    req(hashlib.sha256((ROOT/'原始业务方案_PMOLite.html').read_bytes()).hexdigest()==SOURCE_HASH,'Source business HTML changed')
    data=yaml.safe_load(load('02_PMO_V1_业务规则基线.yaml'))
    a=yaml.safe_load(load('12_OpenAPI_路由请求基线.yaml'))
    m=json.loads(load('11_MCP_工具契约.json'))
    req(data['spec_version']==m['packageSpecVersion']==SPEC, 'spec version mismatch')
    req(a['info']['version']=='1.0.4','API version mismatch')
    req(data['rule_version']==m['ruleVersion']=='lite-v1.0-standalone.3','Business policy version unexpectedly modified')
    req(len(data['states'])==13 and len(data['rule_events'])==8 and len(data['agent']['modes'])==4,'Business coverage missing')
    require_permissions=[
        'new_project_id_appended_to_same_agent_token_scope_atomically',
        'new_project_scope_is_creator_draft_only_not_roles',
        'agent_token_scope_cannot_expand_to_other_projects_without_new_human_grant',
        'agent_token_scope_creation_write_and_project_creation_atomic'
    ]
    for k in require_permissions:req(data['mcp']['org_scoped_project_draft_create'][k] is True,'new project scope defect: '+k)
    req(data['scorecards']['closing']['conditional_close']['exception_scope']=='requirements_only','Unsafe closing waiver')
    req(data['money_effort']['reversal']['no_double_subtraction'],'Cost double subtraction risk')
    req(data['engineering_assurance']['append_only_audit_requires_event_time'],'Audit time not mandatory')

    with (ROOT/'14_数据库字段类型与约束矩阵.csv').open('r',encoding='utf-8-sig',newline='') as f: rr=list(csv.DictReader(f))
    columns={(r['table'],r['field']) for r in rr}
    req(len(columns)==len(rr),'Duplicate database dictionary field')
    domains={r['table'] for r in rr if r['group']=='BUSINESS'}
    technical={r['table'] for r in rr if r['group']=='TECHNICAL'}
    req(len(domains)==16 and len(technical)==17,'Business vs technical table counts shifted')
    critical={
        'audit_logs':['created_at','actor_type','actor_id','request_id','action','result'],
        'idempotency_keys':['created_at','expires_at','status','response_body','response_status','request_hash'],
        'outbox_events':['created_at','next_attempt_at','retry_count','status','locked_until','last_error'],
        'rule_events':['rule_version','created_at','source_refs'],
        'agent_credentials':['org_id','allow_project_create','delegated_by','allowed_project_ids','allowed_actions','expires_at'],
        'cost_entries':['baseline_version','is_current_baseline','budget','actual','forecast_remaining'],
        'cost_postings':['posting_of_id','amount_signed','verified_by','verified_at','status'],
        'reports':['version_no','period_start','period_end','is_current_confirmed'],
        'evidence_files':['created_at','verified_at','quarantine_status','sha256','storage_key'],
        'health_snapshots':['calculated_at','rule_version','input_hash','source_refs']
    }
    for t, cols in critical.items():
        for col in cols:req((t,col) in columns,f'Critical DB field {t}.{col} missing')
    # Assert schema no unspecified arbitrary success data for each MCP tool.
    names=set()
    for t in m['tools']:
        name=t['name'];req(name not in names,f'Duplicate MCP name: {name}');names.add(name)
        Draft202012Validator.check_schema(t['inputSchema'])
        Draft202012Validator.check_schema(t['outputSchema'])
        req(t['outputStatus']=='typed_business_data_design__M0_validate_in_sdk',f'Output DTO maturity status mismatch: {name}')
        sch=t['outputSchema']
        success=next((x for x in sch.get('oneOf',[]) if x.get('properties',{}).get('ok',{}).get('const') is True),None)
        req(success is not None,name+': missing success result')
        dto=success['properties']['data']
        req(dto.get('type')=='object' and dto.get('additionalProperties') is False and len(dto.get('required',[]))>0,name+': unconstrained output data')
        example=sample(success)
        Draft202012Validator(sch,format_checker=FormatChecker()).validate(example)
        bad={**example,'unrecognized_injected_key':'SHOULD_NOT_PASS'}
        req(not Draft202012Validator(sch).is_valid(bad),name+': result schema accepted unexpected top-level data')
        tamper={**example,'data':{**example['data'],'unrecognized_data_key':'SHOULD_NOT_PASS'}}
        req(not Draft202012Validator(sch).is_valid(tamper),name+': result data accepted unexpected field')
        required=dto['required'][0]; deleted={k:v for k,v in example['data'].items() if k!=required}
        req(not Draft202012Validator(sch).is_valid({**example,'data':deleted}),name+': output accepts missing required field')
    req(len(names)==40,'Missing MCP tool contracts')

    operations={}; human_only=0
    for route,verbs in a['paths'].items():
        req(route.startswith('/api/v1/'),f'Bad version prefix {route}')
        for method,op in verbs.items():
            oid=op.get('operationId');req(oid and oid not in operations,f'OpenAPI operation ID issue {oid}')
            operations[oid]=(method.upper(),route)
            for var in re.findall(r'\{([^}]+)\}',route):req(any(p.get('in')=='path' and p.get('name')==var for p in op.get('parameters',[])),f'Unbound path argument {oid}.{var}')
            if op.get('x-forbidAgentEvenViaHttp'):
                human_only+=1
                req(op.get('x-actorTypes')==['human'],f'Agent forbidden human route {oid}')
    req(len(operations)==95 and human_only>=37,'API count or human-only safety baseline changed')
    req(names.issubset(operations),'MCP tool missing REST operation mapping')

    # Source tracing line ranges real, not decorative.
    with (ROOT/'07_原文细粒度覆盖矩阵.csv').open('r',encoding='utf-8-sig',newline='') as f:trace=list(csv.DictReader(f))
    orig=load('原始业务方案_PMOLite.html').splitlines()
    req(len(trace)==102, 'Original fine-grained traceability coverage changed')
    for r in trace:
        start_end=r['source_line_range'].split('-')
        req(all(z.strip().isdigit() for z in start_end),f'Trace line range malformed: {r["id"]}')
        st=int(start_end[0]);ed=int(start_end[-1]);req(1<=st<=ed<=len(orig),f'Trace source lines out of file: {r["id"]}')
    print('FINAL V1.0.4 STATIC HARDENING CHECK PASS')
    print(f'  source sha256={SOURCE_HASH}')
    print(f'  spec={SPEC}, ruleVersion={data["rule_version"]}')
    print(f'  source clauses={len(trace)}, rules={len(data["rule_events"])}, states={len(data["states"])}')
    print(f'  API operations={len(operations)} human-only={human_only}, MCP typed DTOs={len(names)}')
    print(f'  database field declarations={len(rr)}, business tables={len(domains)}, technical tables={len(technical)}')
    print('  negative tests: 40 extra-root, 40 extra-data, 40 missing-required all detected')
    print('  LIMITATIONS: no app code, migrations, real Host tests, or signed business policies verified')

if __name__=='__main__':main()
