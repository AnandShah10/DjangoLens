"""Reads the JSON reports written by `djangolens_runtime.middleware`
(a separate, Django-dependent package — see python/djangolens_runtime/)
and summarizes them across requests.

This module has no Django dependency itself; it's the CLI-side half of
the runtime story, joined to the middleware only by the on-disk JSON
report format. See djangolens_runtime/middleware.py for what each report
contains: {"path", "method", "wall_time", "total_queries",
"total_duration", "duplicate_groups": [...]}.
"""

import json
import os


def load_runtime_reports(reports_dir):
    """Reads every `*.json` file in `reports_dir` as one report. Skips
    (rather than crashes on) any file that isn't valid JSON — a partially
    written report from a crashed request shouldn't take down the whole
    summary."""
    reports = []
    if not os.path.isdir(reports_dir):
        return reports

    for filename in sorted(os.listdir(reports_dir)):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(reports_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                reports.append(json.load(f))
        except (OSError, ValueError):
            continue

    return reports


def summarize_runtime_reports(reports):
    """Returns one summary entry per report, sorted slowest-first, each
    with its single worst duplicate-query group (if any) as `top_issue`
    — a quick "what's the one thing to fix on this endpoint" signal,
    without repeating every group here (the full list is still in the
    raw report if needed)."""
    endpoints = []

    for report in reports:
        duplicate_groups = report.get("duplicate_groups") or []
        top_issue = None
        if duplicate_groups:
            top = duplicate_groups[0]
            top_issue = {
                "count": top.get("count"),
                "file": top.get("file"),
                "line": top.get("line"),
                "message": f"{top.get('count')}x duplicate query from this location — likely N+1",
            }

        endpoints.append({
            "path": report.get("path"),
            "method": report.get("method"),
            "wall_time": report.get("wall_time", 0),
            "total_queries": report.get("total_queries", 0),
            "duplicate_group_count": len(duplicate_groups),
            "top_issue": top_issue,
        })

    endpoints.sort(key=lambda e: e["wall_time"], reverse=True)
    return endpoints
