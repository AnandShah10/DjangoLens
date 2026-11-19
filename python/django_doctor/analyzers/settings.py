"""DJANGO006: Settings Drift Detector (static).

Finds Django settings modules — either a `settings/` package with several
environment files (`base.py`, `development.py`, `production.py`, ...) or
standalone `settings.py` / `settings_*.py` files — and flags a small set of
concrete, high-value risks:

- `DEBUG = True` in a file whose name looks production-like (contains
  "prod"/"production"/"staging"/"stage").
- `SECRET_KEY` hardcoded as a literal string (as opposed to loaded from an
  environment variable or secret manager).
- The same hardcoded `SECRET_KEY` pattern appearing in more than one
  environment file.
- `ALLOWED_HOSTS` containing a `"*"` wildcard in a production-like file.

Secret **values** are never read into a diagnostic — only the fact that a
literal string was assigned. Only top-level (module-scope) assignments are
considered; anything computed inside a function/class, or assigned via
`**` unpacking or attribute access, is intentionally not analyzed rather
than risking a wrong guess — see README "Known limitations" for what this
means in practice (e.g. `SECRET_KEY = os.environ["SECRET_KEY"]` is
correctly not flagged, but so is a hardcoded secret hidden inside a
function that computes it dynamically).
"""

import ast
import os
import types

_PROD_LIKE_HINTS = ("prod", "staging", "stage")
_IGNORED_DIRS = {"node_modules", ".venv", "venv", ".git", "__pycache__", "migrations"}

_SEVERITY_BY_RISK = {"high": "error", "medium": "warning", "low": "information"}


def _looks_prod_like(filename):
    stem = os.path.splitext(filename)[0].lower()
    return any(hint in stem for hint in _PROD_LIKE_HINTS)


def _find_settings_files(project_root):
    """Returns (env_label, filepath) pairs for every settings module found
    under project_root: files inside a directory literally named
    `settings/`, plus any standalone `settings.py` / `settings_*.py`."""
    results = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        in_settings_package = os.path.basename(dirpath) == "settings"
        for filename in sorted(filenames):
            if not filename.endswith(".py") or filename == "__init__.py":
                continue
            if in_settings_package or filename == "settings.py" or filename.startswith("settings_"):
                env_label = os.path.splitext(filename)[0]
                results.append((env_label, os.path.join(dirpath, filename)))
    return results


def _top_level_assigns(tree):
    """Only ast.Module.body — deliberately not ast.walk() — so an
    assignment inside a function/class/if-block is never mistaken for a
    real settings value."""
    assigns = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigns[target.id] = node.value
    return assigns


def _is_literal_true(node):
    return isinstance(node, ast.Constant) and node.value is True


def _is_literal_string(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _contains_wildcard(node):
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_is_literal_string(elt) and elt.value == "*" for elt in node.elts)
    return False


def _diag(filepath, node, message, explanation, suggested_fix, risk):
    return {
        "rule_id": "DJANGO006",
        "analyzer": "settings",
        "severity": _SEVERITY_BY_RISK[risk],
        "confidence": "static",
        "message": message,
        "explanation": explanation,
        "suggested_fix": suggested_fix,
        "file": filepath,
        "line": getattr(node, "lineno", 1),
        "col": getattr(node, "col_offset", 0),
    }


def _literal_bool_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


_SUMMARY_SETTINGS = (
    ("DEBUG", "debug"),
    ("SECRET_KEY", "secret_key_hardcoded"),
    ("ALLOWED_HOSTS", "allowed_hosts_wildcard"),
)


def _module_scope_assigned_names(tree):
    """Every name assigned at module scope, including inside module-level
    `if`/`try`/`with`/`for`/`while` blocks (but NOT inside function or
    class bodies). Used to tell "this file never mentions DEBUG" (so a
    parent's value applies) apart from "this file assigns DEBUG, but to
    something not statically readable" (so the value is unknown and must
    NOT silently fall back to the parent's)."""
    names = set()

    def visit(stmts):
        for stmt in stmts:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
                if isinstance(stmt.target, ast.Name):
                    names.add(stmt.target.id)
            elif isinstance(stmt, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(stmt, field, None) or [])
                for handler in getattr(stmt, "handlers", None) or []:
                    visit(handler.body)

    visit(tree.body)
    return names


def _star_import_labels(tree):
    """Module-level `from <something>.<label> import *` ->
    [(label, lineno, col_offset), ...], in import order. The location is
    kept so a diagnostic can point at the exact import that pulls a risky
    value in."""
    imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and any(a.name == "*" for a in node.names):
            imports.append((node.module.split(".")[-1], node.lineno, node.col_offset))
    return imports


def _lookup_inherited(record, setting_name, key, visiting):
    """Returns (found, value, source_env_label). A setting is "found" in
    a file when that file assigns the name at module scope (see
    `_module_scope_assigned_names`); otherwise parents are searched, last
    star-import first — Python runs imports in order, so a later
    `from .b import *` overrides an earlier `from .a import *`. `visiting`
    guards against a (broken) import cycle between settings files."""
    if setting_name in record["assigned"]:
        return True, record["own"][key], record["env_label"]
    if record["file"] in visiting:
        return False, None, None
    visiting = visiting | {record["file"]}
    for parent in reversed(record["parents"]):
        found, value, source = _lookup_inherited(parent, setting_name, key, visiting)
        if found:
            return True, value, source
    return False, None, None


def _first_hop_location(record, setting_name, key):
    """(lineno, col_offset) of the star-import in `record` through which
    the effective value of `setting_name` arrives, or None. Searched in
    the same last-import-first order as `_lookup_inherited`."""
    for parent, loc in zip(reversed(record["parents"]), reversed(record["parent_locs"])):
        found, _value, _source = _lookup_inherited(parent, setting_name, key, frozenset({record["file"]}))
        if found:
            return loc
    return None


def _build_settings_records(project_root):
    """Parses every discovered settings file into an internal record and
    resolves star-imports between them. Shared by the Visualizer
    (`summarize_settings_files`) and the Drift Detector (`scan_settings`)
    so both always agree on what a file inherits."""
    records = []

    for env_label, filepath in _find_settings_files(project_root):
        record = {
            "env_label": env_label,
            "file": filepath,
            "prod_like": _looks_prod_like(os.path.basename(filepath)),
            "parse_error": False,
            "own": {"debug": None, "secret_key_hardcoded": None, "allowed_hosts_wildcard": None},
            "assigned": set(),
            "star_labels": [],
            "parents": [],
            "parent_locs": [],
        }
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except (SyntaxError, UnicodeDecodeError, OSError):
            record["parse_error"] = True
            records.append(record)
            continue

        assigns = _top_level_assigns(tree)
        debug_node = assigns.get("DEBUG")
        secret_node = assigns.get("SECRET_KEY")
        hosts_node = assigns.get("ALLOWED_HOSTS")
        record["own"] = {
            "debug": _literal_bool_value(debug_node) if debug_node is not None else None,
            "secret_key_hardcoded": _is_literal_string(secret_node) if secret_node is not None else None,
            "allowed_hosts_wildcard": _contains_wildcard(hosts_node) if hosts_node is not None else None,
        }
        record["assigned"] = _module_scope_assigned_names(tree)
        record["star_labels"] = _star_import_labels(tree)
        records.append(record)

    by_dir_label = {(os.path.dirname(r["file"]), r["env_label"]): r for r in records}
    by_label = {}
    for r in records:
        by_label.setdefault(r["env_label"], []).append(r)

    for record in records:
        for label, lineno, col in record["star_labels"]:
            parent = by_dir_label.get((os.path.dirname(record["file"]), label))
            if parent is None:
                candidates = by_label.get(label, [])
                parent = candidates[0] if len(candidates) == 1 else None
            if parent is not None and parent is not record:
                record["parents"].append(parent)
                record["parent_locs"].append((lineno, col))

    return records


def summarize_settings_files(project_root):
    """Returns a list with one entry per discovered settings file, showing
    CURRENT STATE rather than drift/risk diagnostics. Powers the Settings
    Visualizer (`django-doctor settings-overview` / the VS Code webview).

    Each entry has:

    - `env_label`, `file`, `prod_like`, `parse_error`
    - `debug` / `secret_key_hardcoded` / `allowed_hosts_wildcard`: what
      THIS FILE ALONE states. `None` means "unknown" — either the setting
      isn't assigned in this file, or it's assigned to something not
      statically readable (e.g. `DEBUG = os.environ.get(...)`). Never
      conflate `None` with `False`.
    - `extends`: env labels of the settings files this one star-imports
      from (`from .base import *`), in import order, when they resolve to
      another discovered settings file.
    - `effective`: the same three settings after following those
      star-imports, so an env file that inherits `DEBUG` from `base.py`
      without reassigning it shows the real inherited value.
    - `inherited_from`: for each setting, the env label that actually
      supplied the effective value, or `None` when this file supplied it
      itself (or nothing could).

    A setting assigned in this file to a non-literal value stays `None`
    in `effective` — it does NOT fall back to the parent's value, since
    the file overrides it with something unknowable. SECRET_KEY's actual
    value is never read, only whether a literal string was assigned.
    """
    records = _build_settings_records(project_root)

    summaries = []
    for record in records:
        effective = {}
        inherited_from = {}
        for setting_name, key in _SUMMARY_SETTINGS:
            found, value, source = _lookup_inherited(record, setting_name, key, frozenset())
            effective[key] = value if found else None
            inherited_from[key] = source if found and source != record["env_label"] else None

        summaries.append({
            "env_label": record["env_label"],
            "file": record["file"],
            "prod_like": record["prod_like"],
            "debug": record["own"]["debug"],
            "secret_key_hardcoded": record["own"]["secret_key_hardcoded"],
            "allowed_hosts_wildcard": record["own"]["allowed_hosts_wildcard"],
            "extends": [p["env_label"] for p in record["parents"]],
            "effective": effective,
            "inherited_from": inherited_from,
            "parse_error": record["parse_error"],
        })

    return summaries


def analyze_settings_file(env_label, filepath):
    """Returns (diagnostics, has_hardcoded_secret) for one settings file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return [], False

    diagnostics = []
    assigns = _top_level_assigns(tree)
    prod_like = _looks_prod_like(os.path.basename(filepath))

    debug_node = assigns.get("DEBUG")
    if prod_like and debug_node is not None and _is_literal_true(debug_node):
        diagnostics.append(_diag(
            filepath, debug_node,
            message=f"DEBUG=True in production-like settings ('{env_label}')",
            explanation=(
                "DEBUG=True in a production-like environment exposes detailed error pages "
                "(including source code, local variables, and settings) to anyone who can trigger "
                "a server error, and disables several performance/security safeguards."
            ),
            suggested_fix=f"Set DEBUG = False in '{env_label}', or derive it from an environment variable that defaults to False.",
            risk="high",
        ))

    secret_node = assigns.get("SECRET_KEY")
    has_hardcoded_secret = secret_node is not None and _is_literal_string(secret_node)
    if has_hardcoded_secret:
        diagnostics.append(_diag(
            filepath, secret_node,
            message=f"SECRET_KEY is hardcoded in '{env_label}' settings",
            explanation=(
                "SECRET_KEY is assigned as a literal string in source rather than loaded from the "
                "environment. Anyone with read access to this file (including in version control "
                "history) has the key. (The value itself is never included in this diagnostic.)"
            ),
            suggested_fix="Load SECRET_KEY from an environment variable or a secret manager instead of hardcoding it.",
            risk="high" if prod_like else "medium",
        ))

    allowed_hosts_node = assigns.get("ALLOWED_HOSTS")
    if prod_like and allowed_hosts_node is not None and _contains_wildcard(allowed_hosts_node):
        diagnostics.append(_diag(
            filepath, allowed_hosts_node,
            message=f"ALLOWED_HOSTS contains a wildcard in production-like settings ('{env_label}')",
            explanation=(
                "A \"*\" in ALLOWED_HOSTS disables Django's Host header validation, which exists "
                "specifically to prevent Host header poisoning attacks (e.g. cache poisoning, "
                "password-reset link poisoning)."
            ),
            suggested_fix="List the actual production hostname(s) explicitly instead of \"*\".",
            risk="medium",
        ))

    return diagnostics, has_hardcoded_secret


_INHERITED_RISKS = (
    # (SETTING, summary key, message, explanation, suggested fix, risk)
    (
        "DEBUG", "debug",
        "DEBUG=True inherited by production-like settings ('{env}') from '{source}'",
        "'{env}' star-imports from '{source}', which sets DEBUG = True, and '{env}' never reassigns it — "
        "so DEBUG=True reaches this production-like environment. It exposes detailed error pages "
        "(source, local variables, settings) to anyone who can trigger a server error.",
        "Set DEBUG = False explicitly in '{env}' (after the import), or fix it at the source in '{source}'.",
        "high",
    ),
    (
        "ALLOWED_HOSTS", "allowed_hosts_wildcard",
        "ALLOWED_HOSTS wildcard inherited by production-like settings ('{env}') from '{source}'",
        "'{env}' star-imports from '{source}', whose ALLOWED_HOSTS contains a \"*\" wildcard, and '{env}' "
        "never reassigns it — disabling Django's Host header validation in a production-like environment.",
        "List the real production hostname(s) explicitly in '{env}' (after the import), or fix '{source}'.",
        "medium",
    ),
    (
        "SECRET_KEY", "secret_key_hardcoded",
        "Hardcoded SECRET_KEY inherited by production-like settings ('{env}') from '{source}'",
        "'{env}' star-imports from '{source}', which hardcodes SECRET_KEY as a literal, and '{env}' never "
        "reassigns it — so this production-like environment runs on a key that lives in source control. "
        "(The value itself is never included in this diagnostic.)",
        "Load SECRET_KEY from an environment variable or secret manager in '{env}', or fix '{source}'.",
        "high",
    ),
)


def _inherited_risk_diagnostics(project_root):
    """Risks that reach a production-like settings file ONLY through
    `from .base import *`-style inheritance — invisible to
    `analyze_settings_file`, which only inspects each file's own
    assignments. A value the file assigns itself (even to something
    unreadable) is never reported here; that's the file's own business
    and is handled (or deliberately left unknown) elsewhere."""
    diagnostics = []
    for record in _build_settings_records(project_root):
        if record["parse_error"] or not record["prod_like"]:
            continue
        env = record["env_label"]
        for setting_name, key, message, explanation, fix, risk in _INHERITED_RISKS:
            found, value, source = _lookup_inherited(record, setting_name, key, frozenset())
            if not (found and value is True and source != env):
                continue
            loc = _first_hop_location(record, setting_name, key)
            lineno, col = loc if loc else (1, 0)
            fmt = {"env": env, "source": source}
            diagnostics.append(_diag(
                record["file"], types.SimpleNamespace(lineno=lineno, col_offset=col),
                message=message.format(**fmt),
                explanation=explanation.format(**fmt),
                suggested_fix=fix.format(**fmt),
                risk=risk,
            ))
    return diagnostics


def scan_settings(project_root):
    diagnostics = []
    hardcoded_secret_files = []

    for env_label, filepath in _find_settings_files(project_root):
        file_diagnostics, has_hardcoded_secret = analyze_settings_file(env_label, filepath)
        diagnostics.extend(file_diagnostics)
        if has_hardcoded_secret:
            hardcoded_secret_files.append(filepath)

    if len(hardcoded_secret_files) > 1:
        diagnostics.append({
            "rule_id": "DJANGO006",
            "analyzer": "settings",
            "severity": "warning",
            "confidence": "static",
            "message": f"SECRET_KEY is hardcoded in {len(hardcoded_secret_files)} settings files",
            "explanation": (
                "Multiple environment settings files hardcode SECRET_KEY directly in source instead "
                "of loading it from the environment. (Values are never compared or shown — this is "
                "reported purely from each file independently hardcoding one.)"
            ),
            "suggested_fix": "Load SECRET_KEY from an environment variable or secret manager in every environment.",
            "file": hardcoded_secret_files[0],
            "line": 1,
            "col": 0,
        })

    diagnostics.extend(_inherited_risk_diagnostics(project_root))

    return diagnostics
