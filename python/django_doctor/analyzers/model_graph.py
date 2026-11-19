"""Lightweight static model relation graph (no Django runtime).

Builds a map of ModelName -> field_name -> relation kind:
  forward_fk | forward_o2o | reverse_fk | m2m

Used to bias N+1 Code Actions toward select_related vs prefetch_related
when the relation name is known on a model class in the project.
"""

from __future__ import annotations

import ast
import os
from typing import Dict, List, Optional, Tuple


RelationKind = str  # forward_fk | forward_o2o | m2m | unknown


def _name_of(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _model_classes(tree: ast.AST) -> List[ast.ClassDef]:
    out = []
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if _name_of(base) == "Model" or (
                isinstance(base, ast.Attribute) and base.attr == "Model"
            ):
                out.append(node)
                break
    return out


def _field_relations_in_class(cls: ast.ClassDef) -> Dict[str, RelationKind]:
    fields: Dict[str, RelationKind] = {}
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        field_type = _name_of(stmt.value.func)
        if field_type == "ForeignKey":
            fields[target.id] = "forward_fk"
        elif field_type == "OneToOneField":
            fields[target.id] = "forward_o2o"
        elif field_type == "ManyToManyField":
            fields[target.id] = "m2m"
    return fields


def build_model_relation_graph(apps, iter_python_files) -> Dict[str, Dict[str, RelationKind]]:
    """Return {ModelName: {field_name: relation_kind}}."""
    graph: Dict[str, Dict[str, RelationKind]] = {}
    for _app, app_path in apps:
        for filepath in iter_python_files(app_path):
            base = os.path.basename(filepath)
            if base != "models.py" and f"{os.sep}models{os.sep}" not in filepath:
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=filepath)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            for cls in _model_classes(tree):
                graph[cls.name] = _field_relations_in_class(cls)
    return graph


def preferred_optimizer(graph: Dict[str, Dict[str, RelationKind]], model_hint: Optional[str], relation: str) -> str:
    """Return 'select_related' or 'prefetch_related' (default select_related)."""
    if model_hint and model_hint in graph:
        kind = graph[model_hint].get(relation)
        if kind in ("forward_fk", "forward_o2o"):
            return "select_related"
        if kind == "m2m":
            return "prefetch_related"
    # Search all models for a field with this name
    kinds = set()
    for fields in graph.values():
        if relation in fields:
            kinds.add(fields[relation])
    if kinds == {"m2m"}:
        return "prefetch_related"
    if kinds and kinds <= {"forward_fk", "forward_o2o"}:
        return "select_related"
    return "select_related"  # safer default for Code Action isPreferred
