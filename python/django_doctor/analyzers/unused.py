"""DJANGO012 / DJANGO013: Best-effort unused model / unused admin detection.

Static only — never executes project code.

Unused model heuristic:
  A model class defined in models.py that is never referenced by name in
  any other Python file under the discovered apps (imports, attribute
  access, string references in migrations are ignored for simplicity).
  Confidence is low/heuristic because dynamic imports, string-based
  get_model(), and apps outside the discovery set will produce false
  positives.

Unused admin heuristic:
  A ModelAdmin subclass (or bare admin.site.register call) is considered
  "used". Models that have no corresponding register() / @admin.register
  anywhere in the project are reported as missing admin registration
  (informational) — this is the inverse of "unused admin", which is
  harder to define usefully.
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple


def _name_of(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _model_classes_in_file(filepath: str) -> List[Tuple[str, int, int]]:
    """Return (model_name, line, col) for classes that look like models.Model subclasses."""
    results = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return results

    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            base_name = _name_of(base)
            if base_name == "Model" or (isinstance(base, ast.Attribute) and base.attr == "Model"):
                results.append((node.name, node.lineno, node.col_offset))
                break
    return results


def _collect_name_references(filepath: str) -> Set[str]:
    """Collect all Name and Attribute identifiers referenced in a file."""
    names: Set[str] = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return names

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # get_model("app", "ModelName") style — weak signal
            names.add(node.value)
    return names


def _admin_registered_models(filepath: str) -> Set[str]:
    """Return model class names that appear in admin.site.register(...) or
    @admin.register(...) in this file."""
    registered: Set[str] = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return registered

    for node in ast.walk(tree):
        # admin.site.register(Model, ...) or admin.site.register(Model)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and node.args
        ):
            name = _name_of(node.args[0])
            if name:
                registered.add(name)
        # @admin.register(Model) decorator
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and _name_of(dec.func) == "register" and dec.args:
                    name = _name_of(dec.args[0])
                    if name:
                        registered.add(name)
                elif (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "register"
                    and dec.args
                ):
                    name = _name_of(dec.args[0])
                    if name:
                        registered.add(name)
    return registered


def scan_unused(apps, iter_python_files) -> List[Dict[str, Any]]:
    """Detect models that appear defined but never referenced outside their
    defining file, and models that have no admin registration."""
    diagnostics: List[Dict[str, Any]] = []

    # 1. Collect all model definitions: name -> (file, line, col, app)
    model_defs: Dict[str, Tuple[str, int, int, str]] = {}
    all_files: List[str] = []

    for app_name, app_path in apps:
        for filepath in iter_python_files(app_path):
            all_files.append(filepath)
            base = os.path.basename(filepath)
            if base == "models.py" or f"{os.sep}models{os.sep}" in filepath:
                for model_name, line, col in _model_classes_in_file(filepath):
                    # Prefer first definition; collisions across apps are rare in fixtures
                    if model_name not in model_defs:
                        model_defs[model_name] = (filepath, line, col, app_name)

    if not model_defs:
        return diagnostics

    # 2. Collect references from every file EXCEPT the defining models.py
    #    (a model always "references" itself in its own class body).
    reference_counts: Dict[str, int] = defaultdict(int)
    registered_models: Set[str] = set()

    for filepath in all_files:
        names = _collect_name_references(filepath)
        for model_name, (def_file, _, _, _) in model_defs.items():
            if model_name in names and os.path.normpath(filepath) != os.path.normpath(def_file):
                reference_counts[model_name] += 1
        registered_models |= _admin_registered_models(filepath)

    # 3. Emit unused-model diagnostics
    for model_name, (filepath, line, col, app_name) in model_defs.items():
        if reference_counts.get(model_name, 0) == 0:
            diagnostics.append({
                "rule_id": "DJANGO012",
                "analyzer": "unused",
                "severity": "information",
                "confidence": "heuristic",
                "message": f"Possibly unused model: {model_name}",
                "explanation": (
                    f"No references to '{model_name}' were found outside its defining file "
                    f"within discovered apps. This may be dead code, or the model may only "
                    f"be reached via dynamic imports / get_model() / external packages."
                ),
                "suggested_fix": (
                    "Confirm the model is intentionally unused, or wire it into views/"
                    "serializers/admin/tests. Suppress with "
                    "# django-doctor: ignore DJANGO012 if this is a false positive."
                ),
                "file": filepath,
                "line": line,
                "col": col,
                "model": model_name,
                "app": app_name,
            })

        # Missing admin registration (informational)
        if model_name not in registered_models:
            diagnostics.append({
                "rule_id": "DJANGO013",
                "analyzer": "unused",
                "severity": "information",
                "confidence": "heuristic",
                "message": f"Model {model_name} has no admin registration",
                "explanation": (
                    "No admin.site.register(...) or @admin.register(...) referencing this "
                    "model was found in the project. Admin registration is optional; this "
                    "is only a reminder for models that should be editable in Django admin."
                ),
                "suggested_fix": (
                    f"If the model should appear in admin, add "
                    f"admin.site.register({model_name}) or a ModelAdmin subclass."
                ),
                "file": filepath,
                "line": line,
                "col": col,
                "model": model_name,
                "app": app_name,
            })

    return diagnostics
