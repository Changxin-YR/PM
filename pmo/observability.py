"""Operational logs: pass only allowlisted metadata, never request or business payloads."""
import json
import sys
from datetime import UTC, datetime


def emit(event: str, level: str = 'info', **fields: object) -> None:
    print(json.dumps({'event': event, 'level': level, 'at': datetime.now(UTC).isoformat(), **fields},
                     ensure_ascii=True, separators=(',', ':')), file=sys.stderr, flush=True)
