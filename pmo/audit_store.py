"""Audit extensions are kept separate from the frozen 0001 table metadata."""
import re

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from .schema_v1 import audit_logs

audit = sa.table('audit_logs', *(sa.column(col.name, col.type) for col in audit_logs.c),
                 sa.column('source_refs', JSONB(none_as_null=True)))
REF = re.compile(r'[a-z][a-z0-9_-]{0,47}:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?:@[1-9][0-9]{0,18})?')


def stable_refs(references: list[str]) -> list[str]:
    return list(dict.fromkeys(ref for ref in references if REF.fullmatch(ref)))[:100]
