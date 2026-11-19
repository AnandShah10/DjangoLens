"""Test Impact Analyzer (static): given a changed file, finds test modules
that likely need re-running.

Approach
--------
1. Every `.py` file under a discovered app is assigned a dotted module path
   relative to the project root (`orders/models.py` -> `orders.models`).
2. A forward import graph is built via `ast`: for each file, resolve its
   imports to other *known project modules* only (external/stdlib imports
   are not part of this graph — this analyzer only cares about in-project
   dependencies).
3. The graph is reversed and walked breadth-first from the changed file's
   module. Test modules (see `_looks_like_test_module`) found at hop 1
   (something directly imports the changed module) are `high` confidence;
   anything found deeper is `likely` — it's connected, but through enough
   indirection that "this test exercises the changed code" is a weaker
   claim.

This deliberately does NOT understand pytest fixtures, Django's test
discovery/tags, dependency injection, or dynamic imports — a test file
that exercises changed code only through such a mechanism, with no static
import of it (or anything that imports it), will not be found. See README
"Known limitations".
"""

import ast
import os
from collections import defaultdict

from ..indexer import iter_python_files

_TEST_MODULE_HINTS = ("test_", "_test", "tests")


def _module_path_for_file(project_root, filepath):
    """'<project_root>/orders/models.py' -> 'orders.models'.
    '<project_root>/orders/tests/__init__.py' -> 'orders.tests'.

    Note: for apps discovered under a `src/`/`apps/` container (see
    discovery.py), this produces 'src.orders.models' — which usually isn't
    the app's *real* import path (Python code typically imports it as just
    'orders.models', with 'src' added to sys.path). This is a known
    limitation shared with the rest of the src-layout support; it means
    cross-boundary edges between a src-layout app and a flat-layout app
    can be missed. See README.
    """
    rel = os.path.relpath(filepath, project_root)
    if rel.endswith("__init__.py"):
        rel = rel[: -len("__init__.py")].rstrip(os.sep)
    else:
        rel = rel[: -len(".py")]
    return rel.replace(os.sep, ".")


def _looks_like_test_module(module_path):
    last_segment = module_path.split(".")[-1]
    return (
        last_segment.startswith("test_")
        or last_segment.endswith("_test")
        or last_segment == "tests"
        or ".tests." in f".{module_path}."
    )


def _resolve_imports(filepath, known_modules):
    """Returns the subset of `known_modules` that `filepath` imports,
    directly or as `from package import submodule`."""
    targets = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return targets

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in known_modules:
                targets.add(node.module)
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in known_modules:
                    targets.add(candidate)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known_modules:
                    targets.add(alias.name)

    return targets


def _build_reverse_graph(apps, project_root):
    all_files = []
    for _app_name, app_path in apps:
        all_files.extend(iter_python_files(app_path))

    module_of_file = {f: _module_path_for_file(project_root, f) for f in all_files}
    file_of_module = {mod: f for f, mod in module_of_file.items()}
    known_modules = set(file_of_module.keys())

    forward = defaultdict(set)
    for f in all_files:
        forward[module_of_file[f]] |= _resolve_imports(f, known_modules)

    reverse = defaultdict(set)
    for src_module, targets in forward.items():
        for target_module in targets:
            reverse[target_module].add(src_module)

    return reverse, file_of_module


def find_affected_tests(apps, changed_file, project_root):
    """Returns a list of {test_module, test_file, confidence, hops},
    sorted by (confidence, test_module) for deterministic output.
    `changed_file` may be absolute or relative to `project_root`.
    """
    changed_abs = changed_file if os.path.isabs(changed_file) else os.path.join(project_root, changed_file)
    changed_module = _module_path_for_file(project_root, changed_abs)

    reverse, file_of_module = _build_reverse_graph(apps, project_root)

    results = []
    visited = {changed_module}
    frontier = set(reverse.get(changed_module, ()))
    hops = 1

    while frontier:
        next_frontier = set()
        for module in frontier:
            if module in visited:
                continue
            visited.add(module)
            if _looks_like_test_module(module):
                results.append({
                    "test_module": module,
                    "test_file": file_of_module.get(module),
                    "confidence": "high" if hops == 1 else "likely",
                    "hops": hops,
                })
            next_frontier |= reverse.get(module, set())
        frontier = next_frontier
        hops += 1

    results.sort(key=lambda r: (0 if r["confidence"] == "high" else 1, r["test_module"]))
    return results


def _top_level_symbols(filepath):
    """Return set of top-level function and class names defined in filepath."""
    names = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return names
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _imported_symbols_from_module(filepath, target_module):
    """Symbols imported from target_module in filepath (from X import a, b)."""
    symbols = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return symbols
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == target_module or node.module.startswith(target_module + "."):
                for alias in node.names:
                    if alias.name != "*":
                        symbols.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target_module or alias.name.startswith(target_module + "."):
                    symbols.add(alias.name.split(".")[-1])
    return symbols


def find_affected_tests_with_symbols(apps, changed_file, project_root):
    """Like find_affected_tests, but also reports which symbols from the
    changed module each test file imports (symbol-level hint).

    When the changed file defines classes/functions and a test only imports
    a subset, `imported_symbols` lists that subset and `defined_symbols`
    lists everything defined at module top-level in the changed file.
    """
    results = find_affected_tests(apps, changed_file, project_root)
    changed_abs = changed_file if os.path.isabs(changed_file) else os.path.join(project_root, changed_file)
    changed_module = _module_path_for_file(project_root, changed_abs)
    defined = _top_level_symbols(changed_abs)

    for r in results:
        test_file = r.get("test_file")
        imported = set()
        if test_file:
            imported = _imported_symbols_from_module(test_file, changed_module)
            # Also check intermediate hops: if hops>1, still show direct imports from changed if any
        r["defined_symbols"] = sorted(defined)
        r["imported_symbols"] = sorted(imported)
        if defined and imported:
            r["symbol_overlap"] = sorted(defined & imported)
            if imported and not (defined & imported):
                # imports the module but not by specific symbol name overlap — still module-level
                r["symbol_note"] = "imports module but no overlapping top-level symbol names detected"
            elif defined & imported:
                r["symbol_note"] = f"test imports symbols: {', '.join(sorted(defined & imported))}"
        elif defined:
            r["symbol_note"] = "no direct symbol imports from changed module detected in this test file"
        else:
            r["symbol_note"] = ""
    return results
