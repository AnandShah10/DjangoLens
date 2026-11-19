"""Standalone CLI for DjangoLens' static analyzers.

    python -m django_doctor.cli scan <path> [--config FILE] [--format text|json|sarif]

The same code path is invoked by the VS Code extension (see
src/pythonBridge.ts), so results are guaranteed identical between the
editor and CI.
"""

import argparse
import json
import os
import sys

from .discovery import find_django_root, discover_apps
from .indexer import build_cross_app_imports, iter_python_files
from .analyzers.architecture import detect_circular_dependencies, check_architecture_rules
from .analyzers.n1_detector import scan_file_for_n_plus_one
from .analyzers.migrations import scan_migrations
from .analyzers.signals import scan_signals, scan_signal_chains, build_multi_hop_chains
from .analyzers.settings import scan_settings, summarize_settings_files
from .analyzers.urls import discover_urls
from .analyzers.admin import scan_admin
from .analyzers.models_health import scan_models_health
from .analyzers.unused import scan_unused
from .analyzers.deprecated_apis import scan_deprecated_apis
from .analyzers.unused_urls import scan_unused_url_names
from .analyzers.unused_signals import scan_unused_signals
from .analyzers.model_graph import build_model_relation_graph, preferred_optimizer
from .analyzers.test_impact import find_affected_tests, find_affected_tests_with_symbols
from .runtime_report import load_runtime_reports, summarize_runtime_reports
from .analyzers.api_diff import scan_api_diff
from .health_score import compute_health_score
from .suppressions import filter_diagnostics, analyzer_enabled


def load_config(path):
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def run_scan(project_path, config):
    root = find_django_root(project_path) or project_path
    extra = (config or {}).get("discovery", {}).get("extra_app_roots") or []
    apps = discover_apps(root, extra_containers=extra)
    import_records = build_cross_app_imports(root, apps)

    diagnostics = []
    diagnostics.extend(detect_circular_dependencies(import_records))
    diagnostics.extend(check_architecture_rules(import_records, config))

    model_graph = build_model_relation_graph(apps, iter_python_files)
    for _app_name, app_path in apps:
        for filepath in iter_python_files(app_path):
            for d in scan_file_for_n_plus_one(filepath):
                rel = d.get("relation")
                if rel:
                    d["preferred_optimizer"] = preferred_optimizer(model_graph, d.get("model"), rel)
                diagnostics.append(d)

    diagnostics.extend(scan_migrations(apps))
    diagnostics.extend(scan_signals(apps, iter_python_files))
    diagnostics.extend(scan_settings(root))
    diagnostics.extend(scan_admin(apps))
    # New dependency-health / model-field / unused-entity analyzers
    diagnostics.extend(scan_models_health(apps, iter_python_files))
    diagnostics.extend(scan_unused(apps, iter_python_files))
    diagnostics.extend(scan_deprecated_apis(apps, iter_python_files, root))
    diagnostics.extend(scan_unused_url_names(apps, iter_python_files, root))
    diagnostics.extend(scan_unused_signals(apps, iter_python_files))

    # Apply inline + config suppressions and per-analyzer toggles
    diagnostics = filter_diagnostics(diagnostics, config)

    # Stable ordering: by file, then line, so JSON/text output is deterministic.
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))

    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
        "health": compute_health_score(diagnostics),
    }


def run_migration_scan(project_path):
    """Runs only the Migration Risk Analyzer — isolated from architecture/ORM
    diagnostics, matching the `django-doctor migrations` / standalone
    `django-migration-check` use case (a focused pre-deploy check)."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_migrations(apps)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))

    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_signal_scan(project_path):
    """Runs only the Signal Detector."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_signals(apps, iter_python_files)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))

    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_signal_chains(project_path):
    """Runs the signal chain data model (not diagnostics — every
    receiver, risky or not, for visualization)."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    chains = scan_signal_chains(apps, iter_python_files)
    chains.sort(key=lambda c: (c.get("file") or "", c.get("line") or 0))

    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "chain_count": len(chains),
        "chains": chains,
    }


def run_models_health_scan(project_path):
    """Runs only the Model Field Health Analyzer."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_models_health(apps, iter_python_files)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))
    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_unused_scan(project_path):
    """Runs only the unused-model / missing-admin analyzer."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_unused(apps, iter_python_files)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))
    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_multi_hop_signal_chains(project_path):
    """Multi-hop signal chain analysis (best-effort)."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    chains = build_multi_hop_chains(apps, iter_python_files)
    chains.sort(key=lambda c: (c.get("file") or "", c.get("line") or 0))
    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "chain_count": len(chains),
        "chains": chains,
    }


def format_multi_hop_chains_text(result):
    lines = [f"Multi-hop signal chains ({result.get('chain_count', 0)} receivers):", ""]
    for c in result.get("chains", []):
        hops = c.get("hops") or [c]
        head = hops[0]
        lines.append(
            f"  {head.get('sender') or '?'} --{head.get('signal')}--> "
            f"{head.get('receiver')} ({head.get('file')}:{head.get('line')})"
        )
        for hop in hops[1:]:
            reason = hop.get("reason") or "downstream"
            lines.append(
                f"       |--[{reason}]--> {hop.get('sender') or '?'} "
                f"--{hop.get('signal')}--> {hop.get('receiver')} "
                f"({hop.get('file')}:{hop.get('line')})"
            )
        effects = head.get("effects") or []
        if effects:
            lines.append(f"       effects: {', '.join(effects)}")
        lines.append("")
    return "\n".join(lines)


def run_settings_scan(project_path):
    """Runs only the Settings Drift Detector."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_settings(root)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))

    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_admin_scan(project_path):
    """Runs only the Admin UX Analyzer."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_admin(apps)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))

    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_urls_scan(project_path):
    """Runs the URL Explorer. Distinct output shape from the diagnostics
    commands above — endpoints and notes, not severity-graded issues."""
    root = find_django_root(project_path) or project_path
    endpoints, notes = discover_urls(root)
    endpoints.sort(key=lambda e: (e["path"], e.get("file") or ""))

    return {
        "project_root": root,
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
        "notes": notes,
    }


def run_test_impact_scan(project_path, changed_file):
    """Test impact with optional symbol-level overlap hints."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    results = find_affected_tests_with_symbols(apps, changed_file, root)
    return {
        "project_root": root,
        "changed_file": changed_file,
        "affected_tests": results,
        "count": len(results),
    }


def run_runtime_report(reports_dir):
    """Reads and summarizes JSON reports written by the (separate,
    Django-dependent) djangolens_runtime middleware — see
    python/djangolens_runtime/README.md for how to enable it."""
    reports = load_runtime_reports(reports_dir)
    endpoints = summarize_runtime_reports(reports)

    return {
        "reports_dir": reports_dir,
        "request_count": len(reports),
        "endpoints": endpoints,
    }


def run_api_diff_scan(project_path, base_ref, head_ref=None):
    """Runs the API Contract Drift Detector."""
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics, error = scan_api_diff(root, base_ref, head_ref)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("serializer") or ""))

    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "base_ref": base_ref,
        "head_ref": head_ref or "(working tree)",
        "error": error,
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_settings_overview(project_path):
    """Runs the Settings Visualizer's data source — current state, not
    drift diagnostics. Powers `django-doctor settings-overview` and the
    VS Code webview."""
    root = find_django_root(project_path) or project_path
    files = summarize_settings_files(root)

    return {
        "project_root": root,
        "file_count": len(files),
        "files": files,
    }


def format_text(result):
    lines = [
        f"DjangoLens scan: {result['project_root']}",
        f"Apps found: {', '.join(result['apps']) or '(none)'}",
        f"{result['diagnostic_count']} issue(s) found",
    ]
    health = result.get("health")
    if health:
        lines.append(
            f"Health score: {health['score']}/100 "
            f"(see 'deduction_by_rule' in --format json for the breakdown)"
        )
    lines.append("")
    for d in result["diagnostics"]:
        loc = f"{d['file']}:{d['line']}" if "file" in d else "(project-wide)"
        lines.append(f"[{d['rule_id']}] {d['severity'].upper()} ({d['confidence']} confidence) {loc}")
        lines.append(f"  {d['message']}")
        if d.get("explanation"):
            lines.append(f"  {d['explanation']}")
        if d.get("suggested_fix"):
            lines.append(f"  Suggested fix: {d['suggested_fix']}")
        lines.append("")
    return "\n".join(lines)


_EFFECT_LABELS = {
    "recursive_save": "⚠ recursive save",
    "network_call": "⚠ network call",
    "email": "⚠ sends email",
}


def format_signal_chains_text(result):
    lines = [
        f"DjangoLens Signal Chains: {result['project_root']}",
        f"{result['chain_count']} receiver(s) found",
        "",
    ]
    for c in result["chains"]:
        effects = ", ".join(_EFFECT_LABELS.get(e, e) for e in c["effects"]) or "no effects detected"
        lines.append(f"{c['sender'] or '?'} --[{c['signal']}]--> {c['receiver']}")
        lines.append(f"  {c['file']}:{c['line']}")
        lines.append(f"  {effects}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_settings_overview_text(result):
    lines = [
        f"DjangoLens Settings Overview: {result['project_root']}",
        f"{result['file_count']} settings file(s) found",
        "",
    ]

    def _fmt(value, true_label, false_label):
        if value is None:
            return "? (unknown)"
        return true_label if value else false_label

    rows = (
        ("DEBUG", "debug", "True", "False"),
        ("SECRET_KEY", "secret_key_hardcoded", "HARDCODED", "not hardcoded"),
        ("ALLOWED_HOSTS", "allowed_hosts_wildcard", "WILDCARD (*)", "ok"),
    )

    for s in result["files"]:
        prod_tag = " [prod-like]" if s["prod_like"] else ""
        lines.append(f"{s['env_label']}{prod_tag}")
        lines.append(f"  file: {s['file']}")
        if s["parse_error"]:
            lines.append("  (could not parse this file)")
        else:
            if s.get("extends"):
                lines.append(f"  extends: {', '.join(s['extends'])}")
            effective = s.get("effective") or {}
            inherited_from = s.get("inherited_from") or {}
            for name, key, true_label, false_label in rows:
                # Fall back to this file's own value for results that
                # predate the `effective` field.
                value = effective.get(key, s.get(key))
                note = f" (inherited from {inherited_from[key]})" if inherited_from.get(key) else ""
                lines.append(f"  {name}={_fmt(value, true_label, false_label)}{note}")
        lines.append("")

    return "\n".join(lines).rstrip()


def format_urls_text(result):
    lines = [
        f"DjangoLens URL Explorer: {result['project_root']}",
        f"{result['endpoint_count']} endpoint(s) found",
        "",
    ]
    for e in result["endpoints"]:
        loc = f"{e['file']}:{e['line']}" if e.get("file") else ""
        view = e.get("view") or "?"
        name = f" [name={e['name']}]" if e.get("name") else ""
        lines.append(f"{e['path']:<30} -> {view}{name}  {loc}")
    if result["notes"]:
        lines.append("")
        lines.append("Notes:")
        for note in result["notes"]:
            lines.append(f"  {note}")
    return "\n".join(lines)


def format_test_impact_text(result):
    lines = [
        f"DjangoLens Test Impact: {result['changed_file']}",
        f"{result['affected_test_count']} potentially affected test module(s)",
        "",
    ]
    for r in result["affected_tests"]:
        mark = "\u2713" if r["confidence"] == "high" else "?"
        lines.append(f"{mark} {r['test_module']} ({r['confidence']}, {r['hops']} hop(s)) — {r['test_file']}")
    if not result["affected_tests"]:
        lines.append("(no statically-traceable test modules import this file, directly or transitively)")
    return "\n".join(lines)


def format_api_diff_text(result):
    lines = [
        f"DjangoLens API Contract Drift: {result['project_root']}",
        f"Comparing {result['base_ref']} -> {result['head_ref']}",
    ]
    if result.get("error"):
        lines.append("")
        lines.append(f"Could not run: {result['error']}")
        return "\n".join(lines)

    lines.append(f"{result['diagnostic_count']} issue(s) found")
    lines.append("")
    for d in result["diagnostics"]:
        lines.append(f"[{d['rule_id']}] {d['severity'].upper()} ({d['confidence']} confidence) {d.get('serializer', '')} — {d['file']}")
        lines.append(f"  {d['message']}")
        if d.get("explanation"):
            lines.append(f"  {d['explanation']}")
        if d.get("suggested_fix"):
            lines.append(f"  Suggested fix: {d['suggested_fix']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_runtime_report_text(result):
    lines = [
        f"DjangoLens Runtime Report: {result['reports_dir']}",
        f"{result['request_count']} captured request(s)",
        "",
    ]
    if result["request_count"] == 0:
        lines.append(
            "No reports found. Enable djangolens_runtime.middleware.DjangoLensQueryMiddleware "
            "in your Django project's settings first — see python/djangolens_runtime/README.md."
        )
        return "\n".join(lines)

    for e in result["endpoints"]:
        lines.append(f"{e['method']} {e['path']}")
        lines.append(f"  {e['wall_time'] * 1000:.1f}ms, {e['total_queries']} quer{'y' if e['total_queries'] == 1 else 'ies'}")
        if e["top_issue"]:
            issue = e["top_issue"]
            lines.append(f"  ⚠ {issue['message']} — {issue['file']}:{issue['line']}")
        lines.append("")

    return "\n".join(lines).rstrip()


_SARIF_LEVEL_BY_SEVERITY = {"error": "error", "warning": "warning", "information": "note"}

_RULE_DESCRIPTIONS = {
    "DJANGO001": "Circular dependency between Django apps",
    "DJANGOARCH": "Import violates a configured architecture rule",
    "DJANGO003": "Possible N+1 query",
    "DJANGO004": "Risky migration operation",
    "DJANGO005": "Signal recursion risk or synchronous external call in a receiver",
    "DJANGO006": "Settings drift (DEBUG/SECRET_KEY/ALLOWED_HOSTS)",
    "DJANGO007": "ModelAdmin missing list_display",
}


def format_sarif(result):
    """Builds a SARIF 2.1.0 result set — the format GitHub's code scanning
    (and most other CI security/lint dashboards) consume. Diagnostics with
    no `file` are skipped (SARIF results require a physical location);
    this should be rare now that circular-dependency reports attach a
    representative import location too.

    File paths are made relative to `project_root` and forward-slashed,
    since SARIF artifact URIs are conventionally relative and
    OS-independent — an absolute Windows path with backslashes is legal
    SARIF but defeats portability across CI runners.
    """
    project_root = result.get("project_root") or "."
    rules_by_id = {}
    sarif_results = []

    for d in result["diagnostics"]:
        rule_id = d["rule_id"]
        if rule_id not in rules_by_id:
            description = _RULE_DESCRIPTIONS.get(rule_id, d["message"])
            rules_by_id[rule_id] = {
                "id": rule_id,
                "shortDescription": {"text": description},
                "fullDescription": {"text": description},
            }

        filepath = d.get("file")
        if not filepath:
            continue

        try:
            uri = os.path.relpath(filepath, project_root)
        except ValueError:
            uri = filepath
        uri = uri.replace(os.sep, "/")

        sarif_results.append({
            "ruleId": rule_id,
            "level": _SARIF_LEVEL_BY_SEVERITY.get(d["severity"], "warning"),
            "message": {"text": d["message"]},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": {
                        "startLine": max(d.get("line") or 1, 1),
                        "startColumn": max((d.get("col") or 0) + 1, 1),
                    },
                }
            }],
        })

    return {
        "version": "2.1.0",
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "DjangoLens",
                    "informationUri": "https://github.com/AnandShah10/DjangoLens",
                    "version": "0.1.0",
                    "rules": list(rules_by_id.values()),
                }
            },
            "results": sarif_results,
        }],
    }


def _emit(result, args):
    if args.format == "sarif":
        print(json.dumps(format_sarif(result), indent=2))
    elif args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(format_text(result))
    if getattr(args, "strict", False) and any(d["severity"] == "error" for d in result["diagnostics"]):
        return 1
    return 0



def run_deprecated_scan(project_path):
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_deprecated_apis(apps, iter_python_files, root)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))
    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_unused_urls_scan(project_path):
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_unused_url_names(apps, iter_python_files, root)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))
    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def run_unused_signals_scan(project_path):
    root = find_django_root(project_path) or project_path
    apps = discover_apps(root)
    diagnostics = scan_unused_signals(apps, iter_python_files)
    diagnostics.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))
    return {
        "project_root": root,
        "apps": [name for name, _ in apps],
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="django-doctor")
    sub = parser.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="Run all static analyzers (architecture + ORM + migrations) over a project")
    scan_p.add_argument("path", nargs="?", default=".", help="Path to the project (or any subdirectory of it)")
    scan_p.add_argument("--config", default=None, help="Path to djangolens.config.json (default: <path>/djangolens.config.json)")
    scan_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    scan_p.add_argument("--strict", action="store_true", help="Exit with status 1 if any error-severity diagnostic is found")

    mig_p = sub.add_parser("migrations", help="Run only the Migration Risk Analyzer (pre-deploy check)")
    mig_p.add_argument("path", nargs="?", default=".")
    mig_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    mig_p.add_argument("--strict", action="store_true")

    sig_p = sub.add_parser("signals", help="Run only the Signal Detector")
    sig_p.add_argument("path", nargs="?", default=".")
    sig_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    sig_p.add_argument("--strict", action="store_true")

    set_p = sub.add_parser("settings", help="Run only the Settings Drift Detector")
    set_p.add_argument("path", nargs="?", default=".")
    set_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    set_p.add_argument("--strict", action="store_true")

    urls_p = sub.add_parser("urls", help="Discover and print resolved Django URL patterns")
    urls_p.add_argument("path", nargs="?", default=".")
    urls_p.add_argument("--format", choices=["text", "json"], default="text")

    overview_p = sub.add_parser("settings-overview", help="Show current DEBUG/SECRET_KEY/ALLOWED_HOSTS state per settings file (Settings Visualizer)")
    overview_p.add_argument("path", nargs="?", default=".")
    overview_p.add_argument("--format", choices=["text", "json"], default="text")

    chains_p = sub.add_parser("signal-chains", help="Show every Django signal receiver (sender/signal/receiver/effects) for visualization")
    chains_p.add_argument("path", nargs="?", default=".")
    chains_p.add_argument("--format", choices=["text", "json"], default="text")

    admin_p = sub.add_parser("admin", help="Run only the Admin UX Analyzer")
    admin_p.add_argument("path", nargs="?", default=".")
    admin_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    admin_p.add_argument("--strict", action="store_true")

    impact_p = sub.add_parser("test-impact", help="Find test modules likely affected by a changed file")
    impact_p.add_argument("path", nargs="?", default=".", help="Project path (or any subdirectory of it)")
    impact_p.add_argument("changed_file", help="Path to the changed file, absolute or relative to <path>")
    impact_p.add_argument("--format", choices=["text", "json"], default="text")

    runtime_p = sub.add_parser(
        "runtime-report",
        help="Summarize JSON reports written by the djangolens_runtime middleware (see python/djangolens_runtime/README.md)",
    )
    runtime_p.add_argument("reports_dir", help="Directory containing the middleware's JSON report files")
    runtime_p.add_argument("--format", choices=["text", "json"], default="text")

    api_p = sub.add_parser("api-diff", help="Compare DRF serializer fields between two git revisions (API Contract Drift Detector)")
    api_p.add_argument("path", nargs="?", default=".", help="Project path (or any subdirectory of it)")
    api_p.add_argument("--base", dest="base_ref", required=True, help="Git ref to compare FROM (e.g. main, a commit SHA)")
    api_p.add_argument("--head", dest="head_ref", default=None, help="Git ref to compare TO (default: current working tree)")
    api_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    api_p.add_argument("--strict", action="store_true")

    models_p = sub.add_parser("models", help="Run model field health checks (on_delete, null/unique, indexes)")
    models_p.add_argument("path", nargs="?", default=".")
    models_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    models_p.add_argument("--strict", action="store_true")

    unused_p = sub.add_parser("unused", help="Detect possibly unused models and missing admin registrations")
    unused_p.add_argument("path", nargs="?", default=".")
    unused_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")

    dep_p = sub.add_parser("deprecated", help="Scan for deprecated Django APIs")
    dep_p.add_argument("path", nargs="?", default=".")
    dep_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    dep_p.add_argument("--strict", action="store_true")

    uurl_p = sub.add_parser("unused-urls", help="Find URL names that are never reverse()'d")
    uurl_p.add_argument("path", nargs="?", default=".")
    uurl_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")

    usig_p = sub.add_parser("unused-signals", help="Find unregistered signal-like handlers")
    usig_p.add_argument("path", nargs="?", default=".")
    usig_p.add_argument("--format", choices=["text", "json", "sarif"], default="text")

    multihop_p = sub.add_parser(
        "signal-chains-multi",
        help="Multi-hop signal chain analysis (best-effort static linking of downstream saves)",
    )
    multihop_p.add_argument("path", nargs="?", default=".")
    multihop_p.add_argument("--format", choices=["text", "json"], default="text")

    args = parser.parse_args(argv)

    if args.command == "scan":
        config_path = args.config or os.path.join(args.path, "djangolens.config.json")
        config = load_config(config_path)
        result = run_scan(args.path, config)
        return _emit(result, args)

    if args.command == "migrations":
        result = run_migration_scan(args.path)
        return _emit(result, args)

    if args.command == "signals":
        result = run_signal_scan(args.path)
        return _emit(result, args)

    if args.command == "settings":
        result = run_settings_scan(args.path)
        return _emit(result, args)

    if args.command == "admin":
        result = run_admin_scan(args.path)
        return _emit(result, args)

    if args.command == "test-impact":
        result = run_test_impact_scan(args.path, args.changed_file)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(format_test_impact_text(result))
        return 0

    if args.command == "runtime-report":
        result = run_runtime_report(args.reports_dir)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(format_runtime_report_text(result))
        return 0

    if args.command == "api-diff":
        result = run_api_diff_scan(args.path, args.base_ref, args.head_ref)
        if args.format == "sarif":
            print(json.dumps(format_sarif(result), indent=2))
        elif args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(format_api_diff_text(result))
        if args.strict and any(d["severity"] == "error" for d in result["diagnostics"]):
            return 1
        return 0

    if args.command == "urls":
        result = run_urls_scan(args.path)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(format_urls_text(result))
        return 0

    if args.command == "settings-overview":
        result = run_settings_overview(args.path)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(format_settings_overview_text(result))
        return 0

    if args.command == "signal-chains":
        result = run_signal_chains(args.path)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(format_signal_chains_text(result))
        return 0

    if args.command == "models":
        result = run_models_health_scan(args.path)
        return _emit(result, args)

    if args.command == "unused":
        result = run_unused_scan(args.path)
        return _emit(result, args)

    if args.command == "signal-chains-multi":
        result = run_multi_hop_signal_chains(args.path)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(format_multi_hop_chains_text(result))
        return 0


    if args.command == "deprecated":
        result = run_deprecated_scan(args.path)
        return _emit(result, args)

    if args.command == "unused-urls":
        result = run_unused_urls_scan(args.path)
        return _emit(result, args)

    if args.command == "unused-signals":
        result = run_unused_signals_scan(args.path)
        return _emit(result, args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
