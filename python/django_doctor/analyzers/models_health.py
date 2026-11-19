"""DJANGO009 / DJANGO010 / DJANGO011: Model field health checks (static).

Detects common model-definition footguns without executing Django:

- Suspicious on_delete choices (e.g. CASCADE on a high-value relation when
  PROTECT/SET_NULL might be safer; SET_NULL without null=True).
- null=True + unique=True combinations (multiple NULLs allowed, often
  unintended for business keys).
- null=True on CharField/TextField without blank=True (or vice-versa) —
  classic Django form/DB mismatch.
- Missing db_index / index hints on ForeignKey fields that are frequently
  filtered (heuristic: FKs without db_index=True and without an explicit
  indexes= Meta entry are flagged at low confidence).

All diagnostics are heuristic / static confidence. Never claims knowledge
of actual table sizes or query patterns.
"""

from __future__ import annotations

import ast
import os
from typing import Any, Dict, List, Optional, Set, Tuple


_RELATION_FIELDS = {
    "ForeignKey",
    "OneToOneField",
    "ManyToManyField",
}

_CHAR_LIKE = {"CharField", "TextField", "SlugField", "EmailField", "URLField"}


def _name_of(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _kw_value(call: ast.Call, name: str) -> Optional[ast.AST]:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_true(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_false(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _on_delete_name(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    # models.CASCADE / models.SET_NULL / CASCADE (bare import)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    # models.SET_NULL is sometimes written as models.SET_NULL — already covered
    return None


def _field_call_info(node: ast.AST) -> Optional[Tuple[str, ast.Call]]:
    """If `node` is an assignment whose value is a models.<Field>(...) call,
    return (FieldName, Call)."""
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    if not isinstance(target, ast.Name):
        return None
    value = node.value
    if not isinstance(value, ast.Call):
        return None
    field_name = _name_of(value.func)
    if not field_name:
        return None
    # Accept both models.ForeignKey(...) and ForeignKey(...)
    return field_name, value


def _model_classes(tree: ast.AST) -> List[ast.ClassDef]:
    models = []
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, ast.ClassDef):
            continue
        # Heuristic: inherits from something named Model (or models.Model)
        for base in node.bases:
            base_name = _name_of(base)
            if base_name == "Model":
                models.append(node)
                break
            if isinstance(base, ast.Attribute) and base.attr == "Model":
                models.append(node)
                break
    return models


def scan_file_for_model_health(filepath: str) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return diagnostics

    for cls in _model_classes(tree):
        model_name = cls.name
        for stmt in cls.body:
            info = _field_call_info(stmt)
            if not info:
                continue
            field_type, call = info
            field_attr = stmt.targets[0].id  # type: ignore[union-attr]
            line = stmt.lineno
            col = stmt.col_offset

            # --- on_delete checks (relation fields) ---
            if field_type in ("ForeignKey", "OneToOneField"):
                on_delete_node = None
                # positional: ForeignKey(Other, on_delete=...) or ForeignKey(Other, CASCADE)
                if len(call.args) >= 2:
                    on_delete_node = call.args[1]
                on_delete_node = _kw_value(call, "on_delete") or on_delete_node
                on_delete = _on_delete_name(on_delete_node)

                if on_delete is None:
                    diagnostics.append({
                        "rule_id": "DJANGO009",
                        "analyzer": "models_health",
                        "severity": "error",
                        "confidence": "high",
                        "message": f"{model_name}.{field_attr}: ForeignKey/OneToOneField missing on_delete",
                        "explanation": (
                            "Since Django 2.0, on_delete is a required argument for ForeignKey "
                            "and OneToOneField. Omitting it is a TypeError at runtime."
                        ),
                        "suggested_fix": "Add on_delete=models.CASCADE (or PROTECT / SET_NULL / SET_DEFAULT / DO_NOTHING).",
                        "file": filepath,
                        "line": line,
                        "col": col,
                        "model": model_name,
                        "field": field_attr,
                    })
                elif on_delete == "SET_NULL":
                    null_node = _kw_value(call, "null")
                    if not _is_true(null_node):
                        diagnostics.append({
                            "rule_id": "DJANGO009",
                            "analyzer": "models_health",
                            "severity": "error",
                            "confidence": "high",
                            "message": f"{model_name}.{field_attr}: on_delete=SET_NULL requires null=True",
                            "explanation": (
                                "SET_NULL sets the column to NULL when the related object is deleted. "
                                "Without null=True the database will reject the UPDATE."
                            ),
                            "suggested_fix": "Add null=True (and usually blank=True for forms).",
                            "file": filepath,
                            "line": line,
                            "col": col,
                            "model": model_name,
                            "field": field_attr,
                        })
                elif on_delete == "CASCADE":
                    # Soft warning: CASCADE is often correct, but worth reviewing
                    # for "important" looking relation names.
                    important_hints = ("user", "account", "customer", "owner", "organization", "tenant")
                    if any(h in field_attr.lower() for h in important_hints):
                        diagnostics.append({
                            "rule_id": "DJANGO009",
                            "analyzer": "models_health",
                            "severity": "information",
                            "confidence": "heuristic",
                            "message": (
                                f"{model_name}.{field_attr}: CASCADE on_delete for a high-value relation"
                            ),
                            "explanation": (
                                "Deleting the related object will cascade-delete these rows. "
                                "For ownership-style relations, PROTECT or SET_NULL is often safer."
                            ),
                            "suggested_fix": (
                                "Confirm cascade deletion is intentional. Consider models.PROTECT "
                                "or models.SET_NULL (with null=True) if rows should survive."
                            ),
                            "file": filepath,
                            "line": line,
                            "col": col,
                            "model": model_name,
                            "field": field_attr,
                        })

            # --- null / blank / unique combinations ---
            null_node = _kw_value(call, "null")
            blank_node = _kw_value(call, "blank")
            unique_node = _kw_value(call, "unique")

            if _is_true(unique_node) and _is_true(null_node):
                diagnostics.append({
                    "rule_id": "DJANGO010",
                    "analyzer": "models_health",
                    "severity": "warning",
                    "confidence": "high",
                    "message": f"{model_name}.{field_attr}: unique=True with null=True",
                    "explanation": (
                        "SQL UNIQUE allows multiple NULLs. If this field is a business key, "
                        "multiple empty values will be permitted, which is often unintended."
                    ),
                    "suggested_fix": (
                        "If empty values should be disallowed, use null=False (and blank=False). "
                        "If multiple empties are OK, document that decision."
                    ),
                    "file": filepath,
                    "line": line,
                    "col": col,
                    "model": model_name,
                    "field": field_attr,
                })

            if field_type in _CHAR_LIKE:
                if _is_true(null_node) and not _is_true(blank_node):
                    diagnostics.append({
                        "rule_id": "DJANGO010",
                        "analyzer": "models_health",
                        "severity": "warning",
                        "confidence": "heuristic",
                        "message": f"{model_name}.{field_attr}: null=True on {field_type} without blank=True",
                        "explanation": (
                            "CharField/TextField with null=True but blank=False creates a mismatch: "
                            "the DB allows NULL while forms require a value. Prefer blank=True "
                            "(empty string) over null=True for string fields when possible."
                        ),
                        "suggested_fix": "Prefer blank=True (and null=False) for string fields, or set both.",
                        "file": filepath,
                        "line": line,
                        "col": col,
                        "model": model_name,
                        "field": field_attr,
                    })
                if _is_true(blank_node) and _is_false(null_node) is False and null_node is None:
                    # blank=True without explicit null — fine for CharField (empty string).
                    # Only flag if null=True was also set (handled above).
                    pass

            # --- index heuristic on FKs ---
            if field_type in ("ForeignKey", "OneToOneField"):
                db_index_node = _kw_value(call, "db_index")
                # Django defaults FK to db_index=True, so only flag if explicitly False
                if _is_false(db_index_node):
                    diagnostics.append({
                        "rule_id": "DJANGO011",
                        "analyzer": "models_health",
                        "severity": "information",
                        "confidence": "heuristic",
                        "message": f"{model_name}.{field_attr}: ForeignKey with db_index=False",
                        "explanation": (
                            "ForeignKeys default to an index. Explicitly disabling it can hurt "
                            "JOIN and filter performance unless the column is known to be unused "
                            "in lookups."
                        ),
                        "suggested_fix": "Remove db_index=False unless you have measured that the index is unnecessary.",
                        "file": filepath,
                        "line": line,
                        "col": col,
                        "model": model_name,
                        "field": field_attr,
                    })

            # Lookup-ish CharField/SlugField without index or unique
            if field_type in {"CharField", "SlugField", "EmailField"}:
                lookup_hints = ("slug", "code", "sku", "key", "token", "email", "username", "uuid", "external_id")
                if any(h in field_attr.lower() for h in lookup_hints):
                    unique_node = _kw_value(call, "unique")
                    db_index_node = _kw_value(call, "db_index")
                    if not _is_true(unique_node) and not _is_true(db_index_node):
                        diagnostics.append({
                            "rule_id": "DJANGO011",
                            "analyzer": "models_health",
                            "severity": "information",
                            "confidence": "heuristic",
                            "message": f"{model_name}.{field_attr}: lookup-like {field_type} without db_index/unique",
                            "explanation": (
                                f"Field name '{field_attr}' suggests it is used in filters/lookups. "
                                "Without unique=True or db_index=True, queries filtering on this "
                                "column may sequential-scan large tables."
                            ),
                            "suggested_fix": "Add db_index=True, or unique=True if values must be unique.",
                            "file": filepath,
                            "line": line,
                            "col": col,
                            "model": model_name,
                            "field": field_attr,
                        })

    return diagnostics


def scan_models_health(apps, iter_python_files) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    for _app_name, app_path in apps:
        for filepath in iter_python_files(app_path):
            base = os.path.basename(filepath)
            # models.py or anything under a models/ package
            if base == "models.py" or f"{os.sep}models{os.sep}" in filepath or filepath.endswith(f"{os.sep}models.py"):
                diagnostics.extend(scan_file_for_model_health(filepath))
            # Also scan any file that might define models (conservative: only models.py path)
    return diagnostics
