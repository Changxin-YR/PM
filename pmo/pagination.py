"""Bounds for one-based page queries."""


def pg_offset(page: int, size: int) -> int:
    # OFFSET accepts a signed bigint. A larger page is empty, not a query error.
    return min((page - 1) * size, 2**63 - 1)
