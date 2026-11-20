from datetime import date
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from . import schema_v1 as db
from .contracts import RULE_VERSION
from .projects import fingerprint
from .requirement_baselines import active_member


def snapshot(row):
    return {k:v.isoformat() if isinstance(v,date) else str(v) if isinstance(v,(Decimal,UUID)) else v for k,v in row.items()}


def payload(i):
    return fingerprint([str(i.id),str(i.project_id),str(i.requested_by),i.target_type,str(i.target_id),i.base_revision,i.before,i.after,i.reason])


def capture(c,a,i,target):
    c.execute(db.approval_requests.insert().values(id=i.id,project_id=i.project_id,action='baseline_change',status='pending',expected_revision=i.base_revision,
        requested_by=a.person,delegated_by=a.person if a.kind=='agent' else None,payload_hash=payload(i),snapshot_hash=fingerprint(snapshot(target)),rule_version=RULE_VERSION))


def authority(c,a,p,kind):
    if a.kind!='human' or not (active_member(c,p,a.person,'reviewer') or (p.sponsor_id==a.person and active_member(c,p,a.person,'sponsor'))): return None
    return c.scalar(sa.text('''SELECT authority_ref FROM change_approval_grants WHERE project_id=:p AND person_id=:u AND target_type=:kind
        AND revoked_at IS NULL AND expires_at>clock_timestamp() FOR SHARE'''),dict(p=p.id,u=a.person,kind=kind))
