"""URL Explorer (static): discovers Django `urlpatterns` and resolves
`include(...)` chains into a flat list of endpoints with their full path,
view reference, name, and source location.

Resolution strategy
--------------------
1. Look for `ROOT_URLCONF = "some.module"` as a literal string across the
   project's settings files (reusing `analyzers.settings`'s discovery).
2. Resolve that module string to a file path (`some.module` ->
   `some/module.py` under the project root).
3. Parse its `urlpatterns = [...]` list. For each `path()`/`re_path()`/
   `url()` call:
   - If the second argument is `include("other.module")`, resolve that
     module the same way and recurse, concatenating route prefixes.
   - Otherwise, record it as a resolved endpoint.
4. If no `ROOT_URLCONF` can be found (or it doesn't resolve to a real
   file), fall back to parsing every `urls.py` in the project
   independently — still useful, just without cross-file prefix
   concatenation.

What this does NOT do (see README "Known limitations" / "Deferred"):
- `include(router.urls)` (a DRF router's dynamically-generated patterns)
  can't be resolved statically — these are silently skipped rather than
  guessed at.
- HTTP methods aren't derived from the view (that would require
  understanding DRF ViewSets/generic views, which is out of scope here).
- No VS Code sidebar tree UI — this module only produces data; the
  `django-doctor urls` CLI command is the only consumer today.
"""

import ast
import os

from .settings import _find_settings_files

_IGNORED_DIRS = {
    "node_modules", ".venv", "venv", ".git", "__pycache__", "migrations",
    "static", "staticfiles", "templates", "media",
}
_PATTERN_CALL_NAMES = {"path", "re_path", "url"}


def _string_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name_of(node):
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _view_label(node):
    """Best-effort human label for a view reference: a bare function/class
    Name, the trailing attribute of a dotted reference, or (for
    `SomeViewSet.as_view()`) the underlying callee."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _view_label(node.func)
    return None


def _find_root_urlconf_module(project_root):
    for _env_label, filepath in _find_settings_files(project_root):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "ROOT_URLCONF":
                        value = _string_value(node.value)
                        if value:
                            return value
    return None


def _module_to_filepath(project_root, module_name):
    """'config.urls' -> <project_root>/config/urls.py, if it exists."""
    candidate = os.path.join(project_root, module_name.replace(".", os.sep) + ".py")
    return candidate if os.path.isfile(candidate) else None


def _find_urlpatterns(tree):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "urlpatterns":
                    if isinstance(node.value, ast.List):
                        return node.value.elts
    return []


def _resolve_urls_module(project_root, filepath, prefix, visited, endpoints, notes):
    if filepath in visited:
        return  # include() cycles shouldn't happen, but don't loop forever if one does.
    visited.add(filepath)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return

    for elt in _find_urlpatterns(tree):
        if not isinstance(elt, ast.Call) or _call_name_of(elt) not in _PATTERN_CALL_NAMES:
            continue
        if not elt.args:
            continue

        route = _string_value(elt.args[0]) or ""
        target = elt.args[1] if len(elt.args) > 1 else None
        name = None
        for kw in elt.keywords:
            if kw.arg == "name":
                name = _string_value(kw.value)

        if isinstance(target, ast.Call) and _call_name_of(target) == "include":
            include_arg = target.args[0] if target.args else None
            module_name = _string_value(include_arg)
            if module_name is None:
                # Dynamic include, e.g. include(router.urls) — can't be
                # resolved statically without executing project code.
                continue
            nested_path = _module_to_filepath(project_root, module_name)
            if nested_path is None:
                notes.append(
                    f"include('{module_name}') at {filepath}:{elt.lineno} could not be resolved "
                    "to a file under the project root"
                )
                continue
            _resolve_urls_module(project_root, nested_path, prefix + route, visited, endpoints, notes)
        else:
            endpoints.append({
                "path": prefix + route,
                "view": _view_label(target) if target is not None else None,
                "name": name,
                "file": filepath,
                "line": elt.lineno,
                "col": elt.col_offset,
            })


def discover_urls(project_root):
    """Returns (endpoints, notes).

    endpoints: a flat list of {"path", "view", "name", "file", "line", "col"}.
    notes: informational strings about include() targets that couldn't be
    resolved (not diagnostics — nothing here implies the project is wrong).
    """
    endpoints = []
    notes = []

    root_module = _find_root_urlconf_module(project_root)
    if root_module:
        root_file = _module_to_filepath(project_root, root_module)
        if root_file:
            _resolve_urls_module(project_root, root_file, "", set(), endpoints, notes)
            return endpoints, notes
        notes.append(f"ROOT_URLCONF = '{root_module}' did not resolve to a file under the project root")

    # Fallback: no usable ROOT_URLCONF — parse every urls.py independently.
    # Each file is resolved on its own (empty prefix), so this won't
    # concatenate prefixes across include() boundaries between files.
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        if "urls.py" in filenames:
            _resolve_urls_module(project_root, os.path.join(dirpath, "urls.py"), "", set(), endpoints, notes)

    return endpoints, notes
