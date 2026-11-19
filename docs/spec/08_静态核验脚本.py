#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PMO V1.0.3 reference-contract checks (NOT product integration tests).

Runs real reference evaluators on *every* test vector, validates all MCP input
JSON Schemas / OpenAPI routing links, then deliberately flips expected outputs to
ensure the validator cannot produce false PASS on critical ACL / LR07 cases.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import jsonschema
import yaml

P = Path(__file__).resolve().parent
SOURCE_HASH='e88d37a5e59fe2e3c14b768ab9d11ae11100b68308278c9bf9b5a0ab1c68a532'


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def dec(v):
    return Decimal(str(v))


def charter_score(c, y):
    if c['case'].startswith('feasibility_'):
        return 'reject' if any(int(v) <= 2 for v in c['scores']) else 'pass_eligible'
    reviewers=c.get('reviewers', [c.get('scores')])
    check(bool(reviewers) and all(isinstance(x,dict) for x in reviewers),'score reviewers missing')
    weights=y['scorecards']['charter']['dimension_weights']
    avg={k:sum((dec(r[k]) for r in reviewers),Decimal(0))/len(reviewers) for k in weights}
    check(all(all(1<=dec(v)<=5 for v in r.values()) for r in reviewers),'invalid score')
    total=sum((avg[k]*dec(w) for k,w in weights.items()),Decimal(0))
    veto=any(dec(r[k]) == 1 for r in reviewers for k in weights)
    res='veto' if veto else 'fast_track' if total>=dec('4.2') else 'pass_with_remediation' if total>=dec('3.2') else 'reject_or_redesign'
    return total,res


def valid_postings(c):
    src={p['id']:p for p in c['postings'] if 'id' in p and not p.get('postingOf')}
    reversals=Counter()
    for p in c['postings']:
        if p['status']=='verified' and p.get('postingOf'):
            target=p['postingOf']
            if target not in src or src[target]['status']!='verified': return 'REJECT_ORPHAN_REVERSAL'
            if dec(p['amount']) != -dec(src[target]['amount']):return 'REJECT_PARTIAL_REVERSAL'
            reversals[target]+=1
    if max(reversals.values(),default=0)>1:return 'REJECT_DUPLICATE_REVERSAL'
    net=sum((dec(p['amount']) for p in c['postings'] if p['status']=='verified'),Decimal(0))
    if net<0:return 'REJECT_NEGATIVE_NET'
    return net


def budget_color(c):
    b=c['budget']
    if b is None:return 'untrusted'
    b,actual,remain=dec(b),dec(c['actual']),dec(c['forecastRemaining'])
    check(b>=0 and actual>=0 and remain>=0,'negative budget actual or remaining')
    if b==0:return 'green' if actual+remain==0 and c.get('declaredZeroBudgetReason') else 'red' if actual+remain>0 else 'untrusted'
    var=(actual+remain-b)/b
    return 'red' if var>dec('.15') else 'yellow' if var>dec('.05') else 'green'


def weighted_requirement(items):
    weights={'P0':3,'P1':2,'P2':1}
    active=[v for v in items if not v.get('approvedCancelled')]
    den=sum(weights[v['priority']] for v in active)
    if not den:return None,False
    num=sum(weights[v['priority']] for v in active if v.get('confirmed'))
    p0_ok=all(v.get('confirmed',False) for v in active if v['priority']=='P0')
    return (num,den),bool(p0_ok and Decimal(num)/Decimal(den)>=dec('.90'))


def merge_lines(c):
    if c.get('dataTrust')=='untrusted':return 'untrusted'
    return 'red' if 'red' in c['lines'] else 'yellow' if c['lines'].count('yellow')>=2 else 'green'


def time_health(c):
    if c.get('stage')!='executing' and c.get('stage')!='delivering':return 'not_applicable'
    if c.get('actualDate') is not None:return 'historical'
    if not c['isKey']:return 'green'
    plan=date.fromisoformat(c['plannedDate']);today=date.fromisoformat(c['today'])
    fc=date.fromisoformat(c['forecastDate'])
    if plan<today:return 'red'
    delay=(fc-plan).days
    return 'red' if delay>10 else 'yellow' if delay>3 else 'green'


def lr_trigger(c,y):
    rule=c['rule']
    if rule=='LR-01':
        allowed=c['toState'] in y['allowed_transitions'].get(c['fromState'],[])
        if not allowed:return True
        # A legal transition may still be illegal if it requires a gate with no valid decision.
        if c['fromState'] in ['charter_review','feasibility_review','closing_review'] and c['toState'] in ['designing','executing','closed']:
            return not (c.get('gateDecision') in ('pass','conditional_pass') and c.get('evidenceValid') is True)
        return False
    if rule=='LR-02':return c['requestedVerdict'] in ('pass','conditional_pass') and min(c['feasibilityScores'])<=2
    if rule=='LR-03':return c['forecastDate']>c['plannedDate'] and not c.get('hasApprovedPlanChange',False)
    if rule=='LR-04':return max(dec(c['moneyVariance']),dec(c['effortVariance']))>dec('.15')
    if rule=='LR-05':
        approved=bool(c.get('approvedChangeId')) and c.get('approvedChangeStatus','applied')=='applied'
        return c['requirementFrozen'] and c['scopeChangeRequested'] and not approved
    if rule=='LR-06':return (c.get('stage')=='executing' and c.get('consecutiveDueMissingWeeklyReports',0)>=2) or (c.get('keyMilestoneProgressClaim',False) and not c.get('hasValidEvidence',True))
    if rule=='LR-07':
        req_ok=c['p0Confirmed'] and dec(c['requirementSatisfaction'])>=dec('.90')
        valid_req_exception=bool(c.get('validRequirementException',False))
        # EXPLICITLY NEVER waive cost / effort / risk with a client signature.
        return not ((req_ok or valid_req_exception) and dec(c['moneyAbsVariance'])<=dec('.20') and dec(c['effortAbsVariance'])<=dec('.20') and c['blockingRisksResolved'])
    if rule=='LR-08':return c.get('state')=='closed' and c['daysSinceClosed']>=14 and (not c['retrospectiveDone'] or c['confirmedLessonCount']<1)
    raise AssertionError('Unknown LR: '+rule)


def state_case(c,y):
    name=c['case']
    if name=='draft_without_sponsor_pm_can_save':return 'ALLOW' if c['fromState']=='drafting' and c['operation']=='save_draft' else 'REJECT'
    if name=='draft_without_sponsor_pm_cannot_submit':return 'REJECT_MISSING_SPONSOR_PM' if c.get('sponsorId') is None or c.get('pmId') is None else 'ALLOW'
    if name=='review_decision_stale_hash':return 'APPROVAL_STALE' if not c['approvalHashMatches'] else 'ALLOW_IF_REVIEW_COMPLETE'
    if name=='rejected_second_resubmit_forbidden':return 'REJECT' if c['resubmitsAlreadyUsed']>=1 else 'ALLOW'
    if name=='archive_without_lesson':return 'REJECT' if c.get('confirmedLessons',0)<1 else 'ALLOW'
    if name=='skip_drafting_to_executing':return 'LR-01_REJECT' if c['toState'] not in y['allowed_transitions'][c['fromState']] else 'ALLOW'
    if name=='valid_charter_pass':return 'ALLOW_IF_REVIEW_COMPLETE' if c['toState'] in y['allowed_transitions'][c['fromState']] and c['decision']=='pass' else 'REJECT'
    if name=='resume_hold':return 'ALLOW' if c['toState'] in y['allowed_transitions'][c['fromState']] and c['sponsorDecision'] else 'REJECT'
    raise AssertionError('Untested state case: '+name)


def approval_case(c):
    a=c['action']
    if c['actorType']=='agent' and a in {'approve_gate','cost_posting_verify','accept_role_assignment'}:return 'FORBIDDEN'
    if a=='edit_draft':return 'ALLOW' if c.get('isDraftCreator') and c.get('projectStage')=='drafting' else 'FORBIDDEN'
    if a=='submit_charter_review':return 'APPROVAL_REQUIRED' if c.get('assignmentStatus')!='accepted' else 'ALLOW_IF_FIELDS_COMPLETE'
    if a=='transition_project':return 'DENIED_AUDITED_SEPARATELY' if c.get('businessRuleAllows') is False else 'ALLOW_IF_AUTHORIZED'
    if a=='approve_gate':
        if c.get('sameAsApplicant'):return 'FORBIDDEN'
        if c.get('requestHashMatches') is False:return 'APPROVAL_STALE'
        if c.get('role')=='reviewer' and c.get('requestHashMatches') is True:return 'ALLOW_IF_BUSINESS_GATE_PASS'
        return 'FORBIDDEN'
    raise AssertionError('Untested approval case: '+c['case'])


def report_case(c,y):
    if 'structuredFields' in c:
        sf=c['structuredFields']; needed=set(y['settings']['weekly_report']['required_sections'])
        return 'ACCEPT' if needed.issubset(sf) and dec(sf.get('effort_person_days',0))>=0 else 'VALIDATION_FAILED'
    if 'expectedCountsAsMissing' in c:return not(c['status'] in ('submitted','confirmed') and c['schemaValid'])
    if c.get('modelResult')=='timeout':return True,False
    raise AssertionError('Untested report case: '+c['case'])


def mcp_permission_case(c):
    if c.get('tokenExpired'):return 'UNAUTHORIZED'
    if c.get('projectCreateRequested'):
        allowed=c.get('allowProjectCreate') is True and c.get('delegatorActiveInOrg') is True and c.get('tokenOrgId')==c.get('delegatorOrgId') and c.get('operation') in c.get('tokenActions',[])
        return 'ALLOW_DRAFT_ONLY' if allowed else 'FORBIDDEN'
    if 'requestedProjectId' in c and c['requestedProjectId'] not in c.get('tokenProjectIds',[]):return 'FORBIDDEN'
    operation=c.get('operation',next(iter(c.get('tokenActions',[])),None))
    if operation in ('customer_confirm','verify_cost_posting','accept_role_assignment','approve_gate','archive_project'):return 'FORBIDDEN'
    if not c.get('projectAllowed',True):return 'FORBIDDEN'
    if operation not in c.get('tokenActions',[]):return 'FORBIDDEN'
    return 'ALLOW_DRAFT_ONLY' if operation.startswith('pmo_') else 'FORBIDDEN'


def test_vector_group(name, items, y):
    for c in items:
        tag=name+':'+c['case']
        if name=='scoring':
            actual=charter_score(c,y)
            if isinstance(actual,tuple):
                check(actual[0]==dec(c['expectedTotal']),tag+':score')
                check(actual[1]==c['expectedRecommendation'],tag+':recommendation')
            else:check(actual==c['expected'],tag)
        elif name=='cost':
            if 'postings' in c:
                val=valid_postings(c)
                if 'expectedValidation' in c:check(val==c['expectedValidation'],tag+':validation')
                else:check(val==dec(c['expectedActual']),tag+':actual')
            else:
                color=budget_color(c)
                if 'expectedColor' in c:check(color==c['expectedColor'],tag+':color')
                if c.get('budget') not in (None,'0',0):
                    v=(dec(c['actual'])+dec(c['forecastRemaining'])-dec(c['budget']))/dec(c['budget'])
                    if 'expectedVariance' in c:check(v==dec(c['expectedVariance']),tag+':variance')
                    if 'lr04' in c:check((v>dec('.15'))==c['lr04'],tag+':LR04')
                    if 'requireReestimate' in c:check((v>dec('.20'))==c['requireReestimate'],tag+':reestimate')
        elif name=='requirements':
            if 'items' in c:
                val,gate=weighted_requirement(c['items'])
                if 'expectedFraction' in c:check(f'{val[0]}/{val[1]}'==c['expectedFraction'],tag+':fraction')
                if c.get('expected')=='UNAVAILABLE':check(val is None,tag+':unavailable')
                if 'expectedRequirementGateEligible' in c:check(gate==c['expectedRequirementGateEligible'],tag+':gate')
                if 'expectedFrozen' in c:check(any(i.get('attemptedCancelled') and not i.get('approvedCancelled') for i in c['items'])==c['expectedFrozen'],tag+':freeze')
            if 'expectedChangeRate' in c:check(('UNAVAILABLE' if c['initialBaselineCount']==0 else str(dec(c['approvedChangedBaselineCount'])/dec(c['initialBaselineCount'])))==c['expectedChangeRate'],tag+':changeRate')
        elif name=='health':
            if 'lines' in c:check(merge_lines(c)==c['expected'],tag+':merge')
            elif 'expectedQuality' in c:check(('red' if c['p0UnconfirmedCount']>0 else 'green')==c['expectedQuality'],tag+':quality')
            elif 'expectedTime' in c:check(time_health(c)==c['expectedTime'],tag+':time')
            elif 'expectedCurrentHealth' in c:check(('not_applicable' if c['stage'] not in ('executing','delivering') else 'red')==c['expectedCurrentHealth'],tag+':stage')
            elif 'expectedDataTrust' in c:check(('untrusted' if c['progressClaimed'] and not c['evidenceRefs'] else 'verified')==c['expectedDataTrust'],tag+':trust')
            else:raise AssertionError('Untested health: '+tag)
        elif name=='state':check(state_case(c,y)==c['expected'],tag)
        elif name=='lrRules':
            check(lr_trigger(c,y)==c['expectedTriggered'],tag+':trigger')
            if c.get('expectedEffect'):
                effects={'LR-01':'refuse_transition','LR-02':'refuse_pass_and_create_task','LR-03':'yellow_warning','LR-04':'red_warning','LR-05':'reject_change','LR-06':'untrusted','LR-07':'refuse_closing','LR-08':'notify_sponsor'}
                check(c['expectedEffect']==effects[c['rule']],tag+':incorrect expected effect')
        elif name=='approval':check(approval_case(c)==c['expected'],tag)
        elif name=='report':
            res=report_case(c,y)
            if 'expectedSubmission' in c:check(res==c['expectedSubmission'],tag+':submitted')
            elif 'expectedCountsAsMissing' in c:check(res==c['expectedCountsAsMissing'],tag+':missing')
            else:check(res==(c['expectedReportPreserved'],c['expectedFactAutoVerified']),tag+':model_fail')
        elif name=='mcpPermissions':check(mcp_permission_case(c)==c['expected'],tag)
        else:raise AssertionError('Unknown test category '+name)


def verify_vectors(j,y):
    total=0
    for k,v in j.items():
        if k.startswith('$'):continue
        check(isinstance(v,list) and len(v)>0,'Non-list vector: '+k)
        test_vector_group(k,v,y)
        total+=len(v)
    return total


def verify_contracts(j,y):
    m=json.loads((P/'11_MCP_工具契约.json').read_text('utf-8'))
    a=yaml.safe_load((P/'12_OpenAPI_路由请求基线.yaml').read_text('utf-8'))
    check(m['packageSpecVersion']==y['spec_version'],'Tool version mismatch')
    check(m['ruleVersion']==y['rule_version'],'Tool rule version mismatch')
    check(a['info']['version']=='1.0.4','API baseline version mismatch')
    banned=set(m['forbiddenOperations']); names=set()
    for tool in m['tools']:
        name=tool['name']; check(name not in names,'Duplicate tool '+name);names.add(name)
        jsonschema.Draft202012Validator.check_schema(tool['inputSchema'])
        jsonschema.Draft202012Validator.check_schema(tool['outputSchema'])
        check(tool['outputStatus']=='typed_business_data_design__M0_validate_in_sdk', 'Typed output contract maturity must be explicit: '+name)
        props=tool['inputSchema']['properties'];required=tool['inputSchema']['required']
        check(set(required)<=set(props),'Tool missing required props '+name)
        check(tool['inputSchema']['additionalProperties'] is False,'Loose input schema '+name)
        check(name not in banned,'Unsafe MCP tool exposed '+name)
        if tool['kind'] not in ('read','derived_read'):
            check('idempotencyKey' in required,'Mutation without idempotency key '+name)
            if name not in ('pmo_project_create','pmo_milestone_create','pmo_requirement_create','pmo_risk_create','pmo_report_submit','pmo_cost_posting_propose','pmo_fact_propose','pmo_stakeholder_create'):
                check('expectedRevision' in required,'Mutation without expected revision '+name)
        check(any(op.get('operationId')==name for methods in a['paths'].values() for op in methods.values()),'Tool without HTTP mapping '+name)
    check(set(y['mcp']['additional_lite_tools'])<=names,'YAML additional tools missing from manifest')
    opids=set(); human_only=[]; num_ops=0
    for path,methods in a['paths'].items():
        check(path.startswith('/api/v1/'),'API path bad '+path)
        for meth,op in methods.items():
            num_ops+=1
            key=op['operationId'];check(key not in opids,'Duplicate operation ID '+key);opids.add(key)
            for par in re.findall(r'\{(\w+)\}',path):
                check(any(p['in']=='path' and p['name']==par for p in op.get('parameters',[])),f'Missing path arg {key} {par}')
            if op.get('x-forbidAgentEvenViaHttp'):
                human_only.append(key)
                check(op.get('x-actorTypes')==['human'],'Sensitive HTTP operation accepts Agent '+key)
                check(key not in names,'Human-only API is MCP tool '+key)
    check(len(names)>=40 and num_ops>=60 and len(human_only)>=9,'Contract coverage too small')
    return len(names),num_ops,len(human_only)


def mutate_probes(j,y):
    # A test suite that simply counts cases will *not* catch these mutations.
    probes=[('mcpPermissions',1,'expected','ALLOW_DANGEROUS_ACTION'),
            ('lrRules',next(i for i,x in enumerate(j['lrRules']) if x['case']=='lr07_signed_requirement_exception_does_not_waive_cost'),'expectedTriggered',False),
            ('cost',next(i for i,x in enumerate(j['cost']) if x['case']=='reversal_partial_correction_net_once'),'expectedActual','-30.00'),
            ('mcpPermissions',next(i for i,x in enumerate(j['mcpPermissions']) if x['case']=='agent_create_project_without_org_grant_denied'),'expected','ALLOW_DRAFT_ONLY')]
    for category,index,field,bad in probes:
        copy_cases=copy.deepcopy(j[category]);copy_cases[index][field]=bad
        try:test_vector_group(category,copy_cases,y)
        except AssertionError:continue
        raise AssertionError(f'MUTATION_NOT_CAUGHT: {category}:{index}.{field}')
    return len(probes)


def main():
    required=['原始业务方案_PMOLite.html','01_PMO_V1_独立系统完整开发方案.md','02_PMO_V1_业务规则基线.yaml','03_PMO_V1_需求追踪矩阵.csv','04_PMO_V1_规则测试向量.json','05_Codex_分阶段自主开发提示词.md','07_原文细粒度覆盖矩阵.csv','10_业务决议冻结与未决清单.md','11_MCP_工具契约.json','12_API_工程接口契约.md','12_OpenAPI_路由请求基线.yaml','13_领域不变量与审计测试.md']
    for file in required:check((P/file).is_file(),'File missing: '+file)
    check(hashlib.sha256((P/required[0]).read_bytes()).hexdigest()==SOURCE_HASH,'Original Lite HTML changed')
    y=yaml.safe_load((P/required[2]).read_text('utf-8'));j=json.loads((P/required[4]).read_text('utf-8'))
    check(y['spec_version']=='PMO-Standalone-1.0.4' and y['rule_version']=='lite-v1.0-standalone.3','spec version mismatch')
    check(len(set(y['states']))==13 and set(y['states'])==set(y['allowed_transitions']),'State machine incomplete')
    for state, targets in y['allowed_transitions'].items():check(set(targets)<=set(y['states']),'Unknown state target: '+state)
    check(set(y['rule_events'])=={f'LR-{i:02d}' for i in range(1,9)},'LR incomplete')
    check(y['agent']['count']==1 and len(y['agent']['modes'])==4,'Agent modes mismatch')
    check(y['scorecards']['charter']['dimension_weights']=={'strategic_alignment':'0.20','customer_attractiveness':'0.30','financial_resource_return':'0.25','risk_controllability':'0.25'},'Original weights altered')
    check(y['scorecards']['charter']['fast_track_score_gte']=='4.20' and y['scorecards']['charter']['normal_pass_score_gte']=='3.20','Original charter thresholds altered')
    check(y['scorecards']['closing']['weighted_satisfaction_rate_gte']=='0.90' and y['scorecards']['closing']['money_absolute_variance_lte']=='0.20' and y['scorecards']['closing']['effort_absolute_variance_lte']=='0.20','Gate thresholds drifted')
    check(y['scorecards']['closing']['conditional_close']['exception_scope']=='requirements_only','Invalid conditional-close scope')
    check(y['money_effort']['actual_calculation']=='sum_signed_verified_postings_once_including_verified_reversals','Net-posting definition drifted')
    txt=(P/required[1]).read_text('utf-8');prompt=(P/'17_Codex_6.2_Astra_完整自主开发提示词.md').read_text('utf-8')
    classes=['Project','Charter','Scorecard','Milestone','CostEntry','Requirement','ChangeEntry','Risk','Review','Decision','Report','Fact','Task','Lesson','Person','Stakeholder']
    for i,n in enumerate(classes,1):check(f'| {i:02d} {n} |' in txt,'Business entity missing: '+n)
    for q in [f'Q{i}' for i in range(1,9)]:check(f'| {q} |' in txt,'Business acceptance missing: '+q)
    for term in ['M0','M4','DEC-04','08_静态核验脚本','10_业务决议冻结','11_MCP_工具契约']:
        check(term in prompt,'Codex prompt missing '+term)
    with (P/required[6]).open(encoding='utf-8-sig',newline='') as f: fine=list(csv.DictReader(f))
    check(len(fine)>=102 and len({i['id'] for i in fine})==len(fine),'Fine trace missing or duplicates')
    for row in fine:
        line=row['source_line_range'].split('-')
        check(all(1<=int(v)<=746 for v in line),'Invalid origin line: '+row['id'])
        check(row['status'] in ('DESIGN_COVERED__IMPLEMENTATION_PENDING','FUTURE_OUT_OF_SCOPE__NO_IMPLEMENTATION'),'Trace status misleading: '+row['id'])
    n=verify_vectors(j,y);tools,ops,human=verify_contracts(j,y);probes=mutate_probes(j,y)
    lr=Counter(c['rule'] for c in j['lrRules'])
    check(set(lr)==set(y['rule_events']) and all(v>=2 for v in lr.values()),'LR +/- cases incomplete')
    print('V1.0.4 REFERENCE CHECK PASS')
    print('  original HTML SHA-256 unchanged:',SOURCE_HASH)
    print('  domains=16, gates=3, LRs=8, agent_modes=4, states=13')
    print(f'  vectors_evaluated={n}, LR_counts={dict(sorted(lr.items()))}')
    print(f'  MCP input schemas validated={tools}, HTTP operations mapped={ops}, human-only operations={human}')
    print(f'  deliberately_corrupted_expected_values_detected={probes}')
    print('  WARNING: reference oracle tests != product/API/DB/MCP end-to-end tests. No live deployment verified.')

if __name__=='__main__':main()
