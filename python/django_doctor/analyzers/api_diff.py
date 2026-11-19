"""DJANGO008: API Contract Drift Detector (static, git-diff based).

Compares DRF serializer field declarations between two git revisions of
the same file — by default, a base ref you provide vs. the current
working tree — and flags:

- Removed fields: BREAKING (error).
- Field type changes (e.g. CharField -> IntegerField): BREAKING (error).
- A field becoming required (or a new required field with no default):
  a narrower breaking risk (warning) — existing consumers that omit it
  may now fail validation.
- A field becoming optional, or a new optional/read-only field: non-
  breaking (information).

This is 100% static: two versions of source text are fetched via `git
show` and compared via `ast` — no Django, DRF, or project code is ever
imported or executed. "Which frontend/mobile consumers are affected" is
deliberately NOT claimed anywhere here — this only compares field
declarations and has no evidence about what actually calls the API (the
original spec explicitly cautions against fabricating that).
"""

import ast
import os
import subprocess

_IGNORED_DIRS = {"migrations", "__pycache__", "tests", "node_modules", ".venv", "venv"}
_SEVERITY_BY_RISK = {"high": "error", "medium": "warning", "low": "information"}


def _run_git(args, cwd):
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _is_git_repo(cwd):
    return _run_git(["rev-parse", "--is-inside-work-tree"], cwd) is not None


def _git_show(cwd, ref, relative_path):
    """Returns file content at `ref`, or None if the file doesn't exist
    there, `ref` doesn't exist, or this isn't a git repo."""
    posix_path = relative_path.replace(os.sep, "/")
    return _run_git(["show", f"{ref}:{posix_path}"], cwd)


def _name_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_serializer_class(class_node):
    return any("Serializer" in (_name_of(base) or "") for base in class_node.bases)


def _field_info(call_node):
    """For `serializers.CharField(required=False, ...)` returns
    {'type': 'CharField', 'required': bool}. DRF's own default is
    required=True unless read_only or a default is given."""
    field_type = _name_of(call_node.func) or "UnknownField"
    required = True
    has_default = False
    read_only = False

    for kw in call_node.keywords:
        if kw.arg == "required" and isinstance(kw.value, ast.Constant):
            required = bool(kw.value.value)
        elif kw.arg == "default":
            has_default = True
        elif kw.arg == "read_only" and isinstance(kw.value, ast.Constant):
            read_only = bool(kw.value.value)

    if has_default or read_only:
        required = False

    return {"type": field_type, "required": required}


def extract_serializers(source, filepath, model_source_fetcher=None):
    """Returns {class_name: {field_name: {type, required}}} for every
    Serializer-like class declared at module level in `source`.

    `model_source_fetcher`, if given, is a callable
    `(models_filename) -> source_or_None` used to resolve a
    `ModelSerializer`'s `Meta.fields` against the actual model — closing
    the gap where a serializer only lists field NAMES (`fields = ['id',
    'name', 'email']` or `fields = "__all__"`) rather than declaring
    explicit `Field()` objects, which previously meant those fields were
    invisible to comparison entirely. Pass `None` to skip this (only
    explicitly-declared fields are compared, the original behavior).

    The fetcher must return the model source AT THE SAME GIT REF as
    `source` itself — `diff_serializers_file` is responsible for that;
    this function has no concept of git at all, it just calls whatever
    fetcher it's given.
    """
    try:
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, ValueError):
        return {}

    serializers = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and _is_serializer_class(node)):
            continue

        fields = {}
        meta_model_name = None
        meta_fields_node = None

        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Call)
            ):
                fields[stmt.targets[0].id] = _field_info(stmt.value)
            elif isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
                for meta_stmt in stmt.body:
                    if (
                        isinstance(meta_stmt, ast.Assign)
                        and len(meta_stmt.targets) == 1
                        and isinstance(meta_stmt.targets[0], ast.Name)
                    ):
                        if meta_stmt.targets[0].id == "model" and isinstance(meta_stmt.value, ast.Name):
                            meta_model_name = meta_stmt.value.id
                        elif meta_stmt.targets[0].id == "fields":
                            meta_fields_node = meta_stmt.value

        if meta_model_name and meta_fields_node is not None and model_source_fetcher is not None:
            model_source = model_source_fetcher("models.py")
            resolved = _resolve_meta_fields(model_source, meta_model_name, meta_fields_node)
            for name, info in resolved.items():
                fields.setdefault(name, info)  # explicit Field() declarations always win over Meta-derived ones

        serializers[node.name] = fields

    return serializers


def _resolve_meta_fields(model_source, model_name, meta_fields_node):
    """Parses `model_source` for `class <model_name>(...):` and returns
    {field_name: {type, required}} for the field names requested by
    `meta_fields_node` (a `Meta.fields` AST node — either a List/Tuple of
    string literals, or the Constant `"__all__"`). Returns {} if the
    model source is missing, can't be parsed, the model class isn't
    found, or `meta_fields_node` isn't a recognizable shape — never
    guessed at.

    `required` is a heuristic approximation of what DRF would actually
    auto-generate: a model field is treated as not required when it has
    `null=True`, `blank=True`, or a `default=`. This mirrors the same
    heuristic used elsewhere in this project (e.g. the Migration Risk
    Analyzer's AddField check) rather than inventing a new one.
    """
    if model_source is None:
        return {}
    try:
        tree = ast.parse(model_source)
    except (SyntaxError, ValueError):
        return {}

    model_fields = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == model_name:
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and isinstance(stmt.value, ast.Call)
                ):
                    field_type = _name_of(stmt.value.func) or "UnknownField"
                    required = True
                    for kw in stmt.value.keywords:
                        if kw.arg in ("null", "blank") and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            required = False
                        elif kw.arg == "default":
                            required = False
                    model_fields[stmt.targets[0].id] = {"type": field_type, "required": required}
            break

    if not model_fields:
        return {}

    if isinstance(meta_fields_node, ast.Constant) and meta_fields_node.value == "__all__":
        requested_names = set(model_fields) | {"id"}
    elif isinstance(meta_fields_node, (ast.List, ast.Tuple)):
        requested_names = {
            elt.value for elt in meta_fields_node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
    else:
        return {}

    resolved = {}
    for name in requested_names:
        if name == "id":
            resolved["id"] = {"type": "IntegerField", "required": False}
        elif name in model_fields:
            resolved[name] = model_fields[name]
    return resolved


def _diag(filepath, class_name, message, explanation, suggested_fix, risk):
    return {
        "rule_id": "DJANGO008",
        "analyzer": "api",
        "severity": _SEVERITY_BY_RISK[risk],
        "confidence": "static",
        "message": message,
        "explanation": explanation,
        "suggested_fix": suggested_fix,
        "file": filepath,
        "line": 1,
        "col": 0,
        "serializer": class_name,
    }


def compare_serializer(filepath, class_name, old_fields, new_fields):
    diagnostics = []
    old_names, new_names = set(old_fields), set(new_fields)

    for removed in sorted(old_names - new_names):
        diagnostics.append(_diag(
            filepath, class_name,
            message=f"BREAKING: field '{removed}' removed from {class_name}",
            explanation=(
                f"'{removed}' was present in the base revision's {class_name} and is gone now. "
                "Any consumer reading this field will get a missing key / None instead."
            ),
            suggested_fix="Confirm this removal is intentional and coordinated with API consumers, or restore the field.",
            risk="high",
        ))

    for added in sorted(new_names - old_names):
        info = new_fields[added]
        if info["required"]:
            diagnostics.append(_diag(
                filepath, class_name,
                message=f"New required field '{added}' added to {class_name}",
                explanation=(
                    f"'{added}' is required with no default. If this serializer is used for input "
                    "(request bodies), existing clients that don't send it will now fail validation."
                ),
                suggested_fix="Consider required=False with a sensible default, at least for one deprecation cycle.",
                risk="medium",
            ))
        else:
            diagnostics.append(_diag(
                filepath, class_name,
                message=f"Field '{added}' added to {class_name}",
                explanation=f"'{added}' is new but optional/read-only — non-breaking for existing consumers.",
                suggested_fix=None,
                risk="low",
            ))

    for common in sorted(old_names & new_names):
        old_type, new_type = old_fields[common]["type"], new_fields[common]["type"]
        old_required, new_required = old_fields[common]["required"], new_fields[common]["required"]

        if old_type != new_type:
            diagnostics.append(_diag(
                filepath, class_name,
                message=f"BREAKING: field '{common}' type changed from {old_type} to {new_type} in {class_name}",
                explanation="A field's declared type changed. Consumers parsing the response with the old type expectation may break.",
                suggested_fix="Confirm this type change is intentional and version the API if consumers can't adapt immediately.",
                risk="high",
            ))
        elif old_required and not new_required:
            diagnostics.append(_diag(
                filepath, class_name,
                message=f"Field '{common}' in {class_name} is no longer required",
                explanation="Becoming optional is usually backward-compatible — consumers can keep sending it, or now omit it.",
                suggested_fix=None,
                risk="low",
            ))
        elif not old_required and new_required:
            diagnostics.append(_diag(
                filepath, class_name,
                message=f"Field '{common}' in {class_name} became required",
                explanation="Consumers that previously omitted this field may now fail validation.",
                suggested_fix="Consider a default value or a deprecation window before requiring it.",
                risk="medium",
            ))

    return diagnostics


def diff_serializers_file(project_root, relative_path, base_ref, head_ref=None):
    """Compares one file's serializers between `base_ref` and `head_ref`.
    `head_ref=None` means the current working tree (matching `git diff
    <ref>` semantics) rather than a committed revision."""
    old_source = _git_show(project_root, base_ref, relative_path)
    if old_source is None:
        return []  # new file, base ref doesn't have it, or not a git repo

    if head_ref is None:
        try:
            with open(os.path.join(project_root, relative_path), "r", encoding="utf-8") as f:
                new_source = f.read()
        except OSError:
            return []
    else:
        new_source = _git_show(project_root, head_ref, relative_path)
        if new_source is None:
            return []

    full_path = os.path.join(project_root, relative_path)
    relative_dir = os.path.dirname(relative_path)

    def _model_fetcher(ref):
        def fetch(models_filename):
            models_rel = os.path.join(relative_dir, models_filename) if relative_dir else models_filename
            if ref is None:
                try:
                    with open(os.path.join(project_root, models_rel), "r", encoding="utf-8") as f:
                        return f.read()
                except OSError:
                    return None
            return _git_show(project_root, ref, models_rel)
        return fetch

    old_serializers = extract_serializers(old_source, full_path, _model_fetcher(base_ref))
    new_serializers = extract_serializers(new_source, full_path, _model_fetcher(head_ref))

    diagnostics = []
    for class_name in sorted(set(old_serializers) & set(new_serializers)):
        diagnostics.extend(compare_serializer(
            full_path, class_name, old_serializers[class_name], new_serializers[class_name]
        ))
    return diagnostics


def _find_serializer_files(project_root):
    """Walks the whole project for `serializers.py` / `*_serializers.py`
    files directly, rather than relying on `discover_apps()` — a
    serializer-only "api" app (no models.py/apps.py of its own, just
    serializers referencing models from other apps) is a real, common
    DRF pattern that app discovery alone would silently miss."""
    files = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename == "serializers.py" or filename.endswith("_serializers.py"):
                files.append(os.path.join(dirpath, filename))
    return files


def scan_api_diff(project_root, base_ref, head_ref=None):
    """Returns (diagnostics, error). `error` is a human-readable string
    (e.g. "not a git repo") when the comparison couldn't run at all —
    distinct from an empty diagnostics list, which just means no drift
    was found."""
    if not _is_git_repo(project_root):
        return [], "Not a git repository (or git isn't available) — API Contract Drift needs git history to compare against."

    diagnostics = []
    for filepath in _find_serializer_files(project_root):
        relative_path = os.path.relpath(filepath, project_root)
        diagnostics.extend(diff_serializers_file(project_root, relative_path, base_ref, head_ref))

    return diagnostics, None
