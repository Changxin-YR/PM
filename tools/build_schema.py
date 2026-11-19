"""Emit a reviewable, frozen SQLAlchemy Core schema from the audited dictionary."""
import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TYPES = dict(uuid='Uuid', jsonb='JSONB', varchar='String', char='CHAR', boolean='Boolean',
             numeric='Numeric', bigint='BigInteger', smallint='SmallInteger', integer='Integer',
             text='Text', timestamptz='DateTime(timezone=True)', date='Date')
UNIQUE = {
    'projects': ['org_id code'], 'project_members': ['project_id person_id role'],
    'scorecards': ['review_id reviewer_id round'], 'persons': ['user_id'],
    'rule_events': ['project_id rule_id business_period fingerprint'],
    'idempotency_keys': ['actor_id operation idempotency_key'], 'user_roles': ['user_id role'],
}
PARTIAL = {
    'milestones': [('project_id ordinal','NOT is_cancelled')],
    'cost_postings': [('posting_of_id',"status = 'verified' AND posting_of_id IS NOT NULL"),
                      ('project_id type source_fingerprint',"status = 'verified' AND source_fingerprint IS NOT NULL")],
    'reports': [('project_id type period','is_current_confirmed')],
    'cost_entries': [('project_id type category period_start period_end','is_current_baseline')],
}
ENUM = {'enum_project_state':'drafting charter_review rejected designing feasibility_review executing on_hold delivering closing_review closed retrospective terminated archived',
        'human_agent':'human agent','enum_human_agent':'human agent','P0_P1_P2':'P0 P1 P2',
        'draft_pending_verified':'draft pending verified','enum_pending_completed_failed':'pending completed failed',
        'enum_pending_processing_sent_dead_letter':'pending processing sent dead_letter',
        'money_effort':'money effort','enum_money_effort':'money effort','money_or_effort':'money effort',
        'success_denied_failed':'success denied failed','pending_accepted_declined_revoked':'pending accepted declined revoked',
        'requirement_state':'baseline delivered accepted customer_confirmed cancelled','high_medium_low':'high medium low'}

def main():
    with next((ROOT/'docs/spec').glob('14_*.csv')).open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    tables = defaultdict(list)
    edges = []
    for row in rows:
        t,n,s = (row[k] for k in ('table','field','type_and_constraint'))
        m = re.fullmatch(r'(\w+)(\([\d,]+\))?([!?])(.*)',s); assert m
        typ,args,_,rest=m.groups(); args=args or ''; name=f'"{n}"'
        props=[repr(n), 'sa.'+TYPES[typ]+args if typ!='jsonb' else 'JSONB']
        if fk:=re.search(r'fk_(\w+)', rest):
            props.append(f'sa.ForeignKey("{fk[1]}.id")'); edges.append(f'    {fk[1]} ||--o{{ {t} : {n}')
        if n=='id': props.extend(['primary_key=True','server_default=sa.text("gen_random_uuid()")'])
        else: props.append('nullable='+str(row['nullable']=='YES'))
        if ' unique' in rest.split('_when')[0] and re.search(r'\b(unique|unique_project)\b',rest): props.append('unique=True')
        if default:=re.search(r'default[ _](now\(\)|true|false|0|1)',rest,re.I): props.append(f'server_default=sa.text({default[1].lower()!r})')
        tables[t].append('sa.Column('+', '.join(props)+')')
        checks = [f'{name} {x}' for x in ('>=1','>=0','<=20971520','>=period_start') if x in rest]
        if 'nonzero' in rest: checks.append(name+' <> 0')
        if b:=re.search(r'between_(\d+)_(\d+)',rest): checks.append(f'{name} BETWEEN {b[1]} AND {b[2]}')
        if rest.strip() in ENUM: checks.append(name+' IN '+str(tuple(ENUM[rest.strip()].split())))
        for check in checks: tables[t].append(f'sa.CheckConstraint({check!r})')
    out=['"""Frozen revision 0001. Do not edit once deployed."""','import sqlalchemy as sa','from sqlalchemy.dialects.postgresql import JSONB','metadata = sa.MetaData()','']
    for t,items in tables.items():
        for fields in UNIQUE.get(t,[]): items.append('sa.UniqueConstraint('+', '.join(map(repr,fields.split()))+')')
        out.extend([f'{t} = sa.Table({t!r}, metadata,',*['    '+x+',' for x in items],')'])
        for fields,where in PARTIAL.get(t,[]): out.append(f'sa.Index("uq_{t}_{fields.split()[-1]}", '+', '.join(t+'.c.'+f for f in fields.split())+f', unique=True, postgresql_where=sa.text({where!r}))')
        if any(r['table']==t and r['field']=='project_id' for r in rows): out.append(f'sa.Index("ix_{t}_project", {t}.c.project_id)')
        out.append('')
    (ROOT/'pmo/schema_v1.py').write_text('\n'.join(out), encoding='utf-8')
    (ROOT/'docs/erd.md').write_text('# PostgreSQL relationships (revision 0001)\n\n```mermaid\nerDiagram\n'+'\n'.join(edges)+'\n```\n',encoding='utf-8')

if __name__=='__main__': main()
