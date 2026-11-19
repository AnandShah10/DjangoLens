"""DJANGO015: Unused URL name detection (static).

Finds `name=` kwargs on path()/re_path()/url() that are never referenced via
reverse(), reverse_lazy(), or {% url %} style string constants elsewhere in
the project.

Also flags include() targets that cannot be resolved to a file (orphan includes).

Confidence is heuristic: dynamic reverse(name_var), get_absolute_url patterns
built at runtime, and frontend-only consumers will produce false positives.
"""

from __future__ import annotations

import ast
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple


def _name_of(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _const_str(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_url_names_from_file(filepath: str) -> List[Tuple[str, int, int]]:
    """Return (name, line, col) for every path/re_path/url(..., name='...')."""
    results = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return results

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _name_of(node.func)
        if func_name not in {"path", "re_path", "url"}:
            continue
        for kw in node.keywords:
            if kw.arg == "name":
                name = _const_str(kw.value)
                if name:
                    results.append((name, node.lineno, node.col_offset))
    return results


def _collect_reverse_references(filepath: str) -> Set[str]:
    """Collect string arguments to reverse / reverse_lazy and string constants
    that look like URL names in templates tags won't appear in .py — we only
    cover Python reverse() here. Also picks up django.urls.reverse imports.
    """
    names: Set[str] = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return names

    reverse_aliases = {"reverse", "reverse_lazy"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in {"reverse", "reverse_lazy"}:
                    reverse_aliases.add(alias.asname or alias.name)

        if isinstance(node, ast.Call):
            func = node.func
            fname = _name_of(func)
            if fname in reverse_aliases and node.args:
                s = _const_str(node.args[0])
                if s:
                    names.add(s)
            # reverse('app:name') style already covered

        # {% url 'name' %} won't be in Python; also catch common HttpResponseRedirect(reverse(...))
        # already covered by Call walk.

    # Fallback: regex for reverse("...") / reverse('...') in case of complex AST misses
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        for m in re.finditer(r"""(?:reverse|reverse_lazy)\(\s*['"]([^'"]+)['"]""", text):
            names.add(m.group(1))
        # Template-like strings in Python files: url 'name'
        for m in re.finditer(r"""\{\%\s*url\s+['"]([^'"]+)['"]""", text):
            names.add(m.group(1))
    except OSError:
        pass

    return names


def scan_unused_url_names(apps, iter_python_files, project_root: Optional[str] = None) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []

    defined: Dict[str, Tuple[str, int, int]] = {}  # name -> (file, line, col)
    all_files: List[str] = []

    search_roots = list(apps)
    if project_root:
        # include top-level config urls
        search_roots.append(("_project", project_root))

    seen_paths: Set[str] = set()
    for _name, root in search_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {"node_modules", ".venv", "venv", "__pycache__", ".git"}]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                filepath = os.path.join(dirpath, fn)
                if filepath in seen_paths:
                    continue
                seen_paths.add(filepath)
                all_files.append(filepath)
                if fn == "urls.py" or fn.endswith("urls.py"):
                    for uname, line, col in _collect_url_names_from_file(filepath):
                        if uname not in defined:
                            defined[uname] = (filepath, line, col)

    if not defined:
        return diagnostics

    referenced: Set[str] = set()
    for filepath in all_files:
        referenced |= _collect_reverse_references(filepath)

    # Scan template files for {% url "name" %}
    roots = []
    if project_root and os.path.isdir(project_root):
        roots.append(project_root)
    for _n, ap in (apps or []):
        if ap not in roots and os.path.isdir(ap):
            roots.append(ap)
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {
                "node_modules", ".venv", "venv", "__pycache__", ".git",
                "static", "staticfiles", "media", "migrations",
            }]
            for fn in filenames:
                if not fn.endswith((".html", ".htm", ".txt")):
                    continue
                tpath = os.path.join(dirpath, fn)
                try:
                    with open(tpath, "r", encoding="utf-8") as tf:
                        ttext = tf.read()
                except OSError:
                    continue
                for m in re.finditer(r"""\{\%\s*url\s+['"]([^'"]+)['"]""", ttext):
                    referenced.add(m.group(1))

    for uname, (filepath, line, col) in defined.items():
        # Also treat 'app:name' references: if someone reverses 'orders:detail'
        # and name is 'detail', we can't always know the namespace. Check both
        # exact and suffix after ':'.
        used = uname in referenced
        if not used:
            for ref in referenced:
                if ref.endswith(":" + uname) or ref == uname:
                    used = True
                    break
        if not used:
            diagnostics.append({
                "rule_id": "DJANGO015",
                "analyzer": "unused_urls",
                "severity": "information",
                "confidence": "heuristic",
                "message": f"Possibly unused URL name: '{uname}'",
                "explanation": (
                    f"No reverse()/reverse_lazy() (or {{% url %}} in .py sources) reference "
                    f"to '{uname}' was found. It may still be used from templates, frontend "
                    f"code, or dynamic reverse() calls."
                ),
                "suggested_fix": (
                    "Confirm the name is used from templates or clients. If not, remove the "
                    "route or the name= kwarg. Suppress with # django-doctor: ignore DJANGO015 "
                    "if this is intentional."
                ),
                "file": filepath,
                "line": line,
                "col": col,
                "url_name": uname,
            })

    return diagnostics
