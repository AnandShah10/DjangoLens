"""Analyzes captured SQL query events for duplicate/N+1-shaped patterns.

Pure Python, no Django dependency — unlike `instrumentation.py` and
`middleware.py` (which need Django to run), this module is directly
unit-tested in DjangoLens' own test suite (tests/test_query_analysis.py).

An "event" is a dict: {"sql", "duration", "alias", "file", "line",
"function"} — see `instrumentation.QueryCollector` for how these are
produced from a real request.
"""

import re

_QUOTED_LITERAL = re.compile(r"'[^']*'")
_NUMERIC_LITERAL = re.compile(r"\b\d+\b")


def normalize_sql(sql):
    """Collapses whitespace and strips inline literal values, so
    structurally identical queries compare equal. Most DB drivers already
    parameterize queries (so the SQL text is identical across loop
    iterations and only `params` differs) — this normalization mainly
    guards against raw, unparameterized SQL with inline literals."""
    normalized = re.sub(r"\s+", " ", sql or "").strip()
    normalized = _QUOTED_LITERAL.sub("?", normalized)
    normalized = _NUMERIC_LITERAL.sub("?", normalized)
    return normalized


def analyze_events(events):
    """Groups events by (normalized SQL, source file, source line).

    Deliberately requires BOTH the SQL shape and the source location to
    match: the same normalized SQL issued from two different call sites in
    one request is not flagged — that's coincidence (e.g. two different
    endpoints/services happening to run the same query shape), not a loop
    re-issuing the same query.

    Returns {"total_queries", "total_duration", "duplicate_groups"},
    where duplicate_groups is sorted by count, most-repeated first.
    """
    groups = {}
    for event in events:
        key = (normalize_sql(event.get("sql", "")), event.get("file"), event.get("line"))
        groups.setdefault(key, []).append(event)

    duplicate_groups = []
    for (_normalized_sql, file, line), group in groups.items():
        if len(group) < 2:
            continue
        duplicate_groups.append({
            "count": len(group),
            "sql_sample": group[0].get("sql"),
            "file": file,
            "line": line,
            "function": group[0].get("function"),
            "total_duration": sum(e.get("duration", 0) for e in group),
        })

    duplicate_groups.sort(key=lambda g: g["count"], reverse=True)

    return {
        "total_queries": len(events),
        "total_duration": sum(e.get("duration", 0) for e in events),
        "duplicate_groups": duplicate_groups,
    }
