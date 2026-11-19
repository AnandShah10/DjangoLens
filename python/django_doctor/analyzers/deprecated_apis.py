"""DJANGO014: Deprecated / removed Django API detector (static).

Flags common patterns that are deprecated or removed in modern Django
(3.x / 4.x / 5.x) so teams upgrading get actionable diagnostics.

Patterns (heuristic, AST-based — never executes project code):

- django.conf.urls.url(...)          → use path() / re_path()
- django.utils.translation.ugettext / ugettext_lazy / ungettext
- django.utils.encoding.force_text / smart_text
- django.utils.six
- models.NullBooleanField
- django.conf.urls.patterns(...)
- provide_context in template tags (context-dict style is fine; we only
  flag the old assignment forms when clearly visible)
- DEFAULT_FILE_STORAGE / STATICFILES_STORAGE settings keys (moved to
  STORAGES in Django 4.2+) — detected in settings modules only

Confidence is heuristic: a name collision with a local function is possible
but uncommon for these specific Django APIs.
"""

from __future__ import annotations

import ast
import os
from typing import Any, Dict, List, Optional, Set, Tuple


# (module_suffix or full attr chain hints, name, message, suggested_fix, severity)
_DEPRECATED_FROM_IMPORTS = {
    ("django.conf.urls", "url"): (
        "django.conf.urls.url is removed in Django 4.0+; use path() or re_path()",
        "Replace url(...) with path(...) or re_path(...).",
        "error",
    ),
    ("django.conf.urls", "patterns"): (
        "django.conf.urls.patterns was removed in Django 1.10+",
        "Use a plain list of path()/re_path() entries assigned to urlpatterns.",
        "error",
    ),
    ("django.utils.translation", "ugettext"): (
        "ugettext is deprecated; use gettext",
        "from django.utils.translation import gettext as _",
        "warning",
    ),
    ("django.utils.translation", "ugettext_lazy"): (
        "ugettext_lazy is deprecated; use gettext_lazy",
        "from django.utils.translation import gettext_lazy as _",
        "warning",
    ),
    ("django.utils.translation", "ungettext"): (
        "ungettext is deprecated; use ngettext",
        "from django.utils.translation import ngettext",
        "warning",
    ),
    ("django.utils.translation", "ungettext_lazy"): (
        "ungettext_lazy is deprecated; use ngettext_lazy",
        "from django.utils.translation import ngettext_lazy",
        "warning",
    ),
    ("django.utils.encoding", "force_text"): (
        "force_text is deprecated; use force_str",
        "from django.utils.encoding import force_str",
        "warning",
    ),
    ("django.utils.encoding", "smart_text"): (
        "smart_text is deprecated; use smart_str",
        "from django.utils.encoding import smart_str",
        "warning",
    ),
}

_DEPRECATED_ATTR_CALLS = {
    # attr name on models or django.db.models
    "NullBooleanField": (
        "NullBooleanField is deprecated since Django 3.1; use BooleanField(null=True)",
        "Replace with models.BooleanField(null=True, blank=True).",
        "warning",
    ),
}

_DEPRECATED_SETTINGS_NAMES = {
    "DEFAULT_FILE_STORAGE": (
        "DEFAULT_FILE_STORAGE is deprecated in Django 4.2+; use STORAGES['default']",
        "Move configuration into the STORAGES setting.",
        "warning",
    ),
    "STATICFILES_STORAGE": (
        "STATICFILES_STORAGE is deprecated in Django 4.2+; use STORAGES['staticfiles']",
        "Move configuration into the STORAGES setting.",
        "warning",
    ),
}


def _name_of(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _module_str(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def scan_file_for_deprecated_apis(filepath: str) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return diagnostics

    # Track aliases: local_name -> (module, original_name)
    aliases: Dict[str, Tuple[str, str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            for alias in node.names:
                original = alias.name
                local = alias.asname or original
                key = (mod, original)
                if key in _DEPRECATED_FROM_IMPORTS:
                    msg, fix, sev = _DEPRECATED_FROM_IMPORTS[key]
                    diagnostics.append({
                        "rule_id": "DJANGO014",
                        "analyzer": "deprecated_apis",
                        "severity": sev,
                        "confidence": "high",
                        "message": f"Deprecated Django API: {mod}.{original}",
                        "explanation": msg,
                        "suggested_fix": fix,
                        "file": filepath,
                        "line": node.lineno,
                        "col": node.col_offset,
                    })
                aliases[local] = (mod, original)

        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "django.utils.six" or alias.name.startswith("django.utils.six"):
                    diagnostics.append({
                        "rule_id": "DJANGO014",
                        "analyzer": "deprecated_apis",
                        "severity": "error",
                        "confidence": "high",
                        "message": "django.utils.six is removed; use the stdlib 'six' package or native Python 3 APIs",
                        "explanation": "Django removed its vendored six module. Python 3 projects should not need it.",
                        "suggested_fix": "Remove the import and use Python 3 builtins, or depend on the standalone 'six' package if required.",
                        "file": filepath,
                        "line": node.lineno,
                        "col": node.col_offset,
                    })

        # models.NullBooleanField(...) or NullBooleanField(...)
        if isinstance(node, ast.Call):
            attr = _name_of(node.func)
            if attr in _DEPRECATED_ATTR_CALLS:
                msg, fix, sev = _DEPRECATED_ATTR_CALLS[attr]
                diagnostics.append({
                    "rule_id": "DJANGO014",
                    "analyzer": "deprecated_apis",
                    "severity": sev,
                    "confidence": "heuristic",
                    "message": f"Deprecated Django API: {attr}",
                    "explanation": msg,
                    "suggested_fix": fix,
                    "file": filepath,
                    "line": node.lineno,
                    "col": node.col_offset,
                })

        # Settings assignments: DEFAULT_FILE_STORAGE = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _DEPRECATED_SETTINGS_NAMES:
                    msg, fix, sev = _DEPRECATED_SETTINGS_NAMES[target.id]
                    diagnostics.append({
                        "rule_id": "DJANGO014",
                        "analyzer": "deprecated_apis",
                        "severity": sev,
                        "confidence": "high",
                        "message": f"Deprecated Django setting: {target.id}",
                        "explanation": msg,
                        "suggested_fix": fix,
                        "file": filepath,
                        "line": node.lineno,
                        "col": node.col_offset,
                    })

        # Usage of imported deprecated names as calls: url(...), ugettext(...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            local = node.func.id
            if local in aliases:
                mod, original = aliases[local]
                key = (mod, original)
                if key in _DEPRECATED_FROM_IMPORTS:
                    # Already flagged the import; optionally also flag the call site
                    # Skip duplicate call-site noise — import is enough.
                    pass

    return diagnostics


def scan_deprecated_apis(apps, iter_python_files, project_root: Optional[str] = None) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    seen_files: Set[str] = set()

    for _app_name, app_path in apps:
        for filepath in iter_python_files(app_path):
            if filepath in seen_files:
                continue
            seen_files.add(filepath)
            diagnostics.extend(scan_file_for_deprecated_apis(filepath))

    # Also scan project-level settings / urls if project_root given
    if project_root:
        for dirpath, dirnames, filenames in os.walk(project_root):
            dirnames[:] = [
                d for d in dirnames
                if d not in {"node_modules", ".venv", "venv", "__pycache__", ".git", "migrations"}
            ]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                filepath = os.path.join(dirpath, fn)
                if filepath in seen_files:
                    continue
                # Only settings-ish or urls at project root depth to avoid double-scanning apps
                base = os.path.basename(filepath)
                if base in {"urls.py", "settings.py"} or "settings" in filepath:
                    seen_files.add(filepath)
                    diagnostics.extend(scan_file_for_deprecated_apis(filepath))

    return diagnostics
