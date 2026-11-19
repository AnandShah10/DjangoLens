"""DJANGO007: Admin UX Analyzer (static).

Finds `ModelAdmin` registrations — both `@admin.register(Model)` and the
imperative `admin.site.register(Model, XAdmin)` form — and flags several
things, each with a concrete, field-accurate suggested fix (parsed from
the model's real `models.py`, never a generic placeholder):

- No `list_display`: the admin list view falls back to each row's
  `__str__()`.
- No `list_filter`, when the model has at least one field of a type
  that's a common, high-value filter candidate (`BooleanField`,
  `ForeignKey`, `ManyToManyField`), OR any field with a `choices=` kwarg
  (a `ChoiceField`-style pattern — e.g. `status = models.CharField(...,
  choices=STATUS_CHOICES)` — regardless of the field's own type).
- No `search_fields`, when the model has a text-like field
  (`CharField`/`TextField`/`EmailField`/`SlugField`/`URLField`) — without
  it, the admin's search box does nothing at all.
- No `date_hierarchy`, when the model has a `DateField`/`DateTimeField` —
  suggests the first one found as convenient date-drilldown navigation.
- No `readonly_fields`, when the model has a field with `auto_now=True`
  or `auto_now_add=True` — these are auto-managed by Django and can't be
  meaningfully edited in the admin form; marking them read-only avoids a
  confusing, silently-ignored edit.

`autocomplete_fields` is implemented too, but needs whole-project
context none of the others do: a relational field is only suggested when
the *related* model has its own admin registered somewhere in the
project (elsewhere is fine — `scan_admin()` collects registrations
across every app first). `analyze_admin_file()` alone, called on a
single file in isolation, won't see registrations from other files —
pass `registered_model_names` explicitly if you need that (see its
docstring).
"""

import ast
import os

_SEVERITY = "information"
_CONFIDENCE = "heuristic"
_LIST_FILTER_CANDIDATE_TYPES = {"BooleanField", "ForeignKey", "ManyToManyField"}
_SEARCH_FIELD_TYPES = {"CharField", "TextField", "EmailField", "SlugField", "URLField"}
_DATE_FIELD_TYPES = {"DateField", "DateTimeField"}
_RELATIONAL_FIELD_TYPES = {"ForeignKey", "ManyToManyField", "OneToOneField"}


def _name_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_auto_timestamp(call_node):
    for kw in call_node.keywords:
        if kw.arg in ("auto_now", "auto_now_add") and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _has_choices(call_node):
    """True when the field call has a `choices=` kwarg at all — the value
    isn't inspected (it's usually a module-level tuple/list/enum
    reference, not a literal here), just its presence, which is enough to
    know Django will render this as a constrained-choice field."""
    return any(kw.arg == "choices" for kw in call_node.keywords)


def _relation_target_model(call_node):
    """For `ForeignKey(User, ...)`, `ForeignKey('auth.User', ...)`, or
    `ForeignKey(to='auth.User', ...)`, returns the bare model name (e.g.
    'User'). Returns None for `'self'` (self-referential FKs have no
    separate admin to link to) or when the target can't be resolved."""
    target = call_node.args[0] if call_node.args else None
    if target is None:
        for kw in call_node.keywords:
            if kw.arg == "to":
                target = kw.value
                break
    if target is None:
        return None

    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        name = target.value.split(".")[-1]
        return None if name == "self" else name
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _is_abstract_model(class_node):
    """True when the class body has `class Meta: abstract = True`."""
    for stmt in class_node.body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
            for meta_stmt in stmt.body:
                if (
                    isinstance(meta_stmt, ast.Assign)
                    and len(meta_stmt.targets) == 1
                    and isinstance(meta_stmt.targets[0], ast.Name)
                    and meta_stmt.targets[0].id == "abstract"
                    and isinstance(meta_stmt.value, ast.Constant)
                    and meta_stmt.value.value is True
                ):
                    return True
    return False


def _class_own_fields(class_node):
    """Fields assigned directly in this class's body (not inherited)."""
    fields = []
    for stmt in class_node.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Call)
        ):
            field_type = _name_of(stmt.value.func)
            relation_target = (
                _relation_target_model(stmt.value) if field_type in _RELATIONAL_FIELD_TYPES else None
            )
            fields.append((
                stmt.targets[0].id,
                field_type,
                _is_auto_timestamp(stmt.value),
                relation_target,
                _has_choices(stmt.value),
            ))
    return fields


def _find_model_fields(app_path, model_name):
    """Parses `<app_path>/models.py` for `class <model_name>(...):` and
    returns [(field_name, field_type, is_auto_timestamp,
    relation_target_model, has_choices), ...] for fields assigned at
    class-body level via `field = models.XField(...)`, in source order.
    `field_type` is the Field class name (e.g. 'ForeignKey') or None if
    it couldn't be resolved from a simple call shape. `is_auto_timestamp`
    is True when the field has `auto_now=True` or `auto_now_add=True`.
    `relation_target_model` is the bare related-model name for
    ForeignKey/ManyToManyField/OneToOneField fields, else None.
    `has_choices` is True when the field has a `choices=` kwarg, of any
    field type.

    Also follows abstract base classes defined in the SAME file (a
    `class Base(models.Model): ... class Meta: abstract = True`, then
    `class Order(Base): ...`) — abstract-base fields come first, then the
    subclass's own, matching Django's real field ordering. This is
    deliberately limited to abstract bases: a concrete (multi-table
    inheritance) base keeps its fields on a separate table, not copied
    onto the child, so following those would misreport the child's fields.
    A base class defined in a different file isn't followed (see README
    "Known limitations").

    Returns [] if the model or its fields can't be found — never guessed
    at."""
    models_file = os.path.join(app_path, "models.py")
    if not os.path.isfile(models_file):
        return []
    try:
        with open(models_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=models_file)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    class_defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    target = class_defs.get(model_name)
    if target is None:
        return []

    visited = set()
    collected = []

    def _collect(node):
        if node.name in visited:
            return  # also guards against any (invalid) cyclic inheritance
        visited.add(node.name)
        for base in node.bases:
            base_name = _name_of(base)
            base_node = class_defs.get(base_name) if base_name else None
            if base_node is not None and _is_abstract_model(base_node):
                _collect(base_node)
        collected.extend(_class_own_fields(node))

    _collect(target)
    return collected


def _has_attr(admin_class_node, name):
    return any(
        isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in stmt.targets
        )
        for stmt in admin_class_node.body
    )


def _find_admin_registrations(tree):
    """Returns [(admin_class_node, model_name), ...] covering both
    registration styles."""
    registrations = []
    class_defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    # @admin.register(Model) decorator form
    for admin_node in class_defs.values():
        for decorator in admin_node.decorator_list:
            if isinstance(decorator, ast.Call) and _name_of(decorator.func) == "register" and decorator.args:
                model_name = _name_of(decorator.args[0])
                if model_name:
                    registrations.append((admin_node, model_name))

    # admin.site.register(Model, XAdmin) imperative form
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and len(node.args) >= 2
        ):
            model_name = _name_of(node.args[0])
            admin_class_name = _name_of(node.args[1])
            admin_node = class_defs.get(admin_class_name) if admin_class_name else None
            if admin_node and model_name:
                registrations.append((admin_node, model_name))

    return registrations


def _format_str_tuple(items):
    """Formats a Python tuple literal of string items, correctly handling
    the single-item case: `('x')` is just a parenthesized string, not a
    tuple — it needs a trailing comma, `('x',)`, to actually be one."""
    reprs = [repr(item) for item in items]
    if len(reprs) == 1:
        return f"({reprs[0]},)"
    return f"({', '.join(reprs)})"


def _diag(filepath, app_name, model_name, admin_node, message, explanation, suggested_fix):
    return {
        "rule_id": "DJANGO007",
        "analyzer": "admin",
        "severity": _SEVERITY,
        "confidence": _CONFIDENCE,
        "message": message,
        "explanation": explanation,
        "suggested_fix": suggested_fix,
        "file": filepath,
        "line": admin_node.lineno,
        "col": admin_node.col_offset,
        "app": app_name,
        "model": model_name,
    }


def analyze_admin_file(app_name, app_path, filepath, registered_model_names=frozenset()):
    diagnostics = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return diagnostics

    seen_classes = set()
    for admin_node, model_name in _find_admin_registrations(tree):
        if admin_node.name in seen_classes:
            continue  # avoid double-reporting if registered both ways
        seen_classes.add(admin_node.name)

        fields_with_types = None  # lazily parsed, shared between the checks below

        if not _has_attr(admin_node, "list_display"):
            fields_with_types = _find_model_fields(app_path, model_name)
            field_names = [name for name, _type, _auto, _rel, _choices in fields_with_types]
            suggested_fields = ["id"] + [f for f in field_names if f != "id"][:4]
            suggested_fix = (
                f"list_display = {_format_str_tuple(suggested_fields)}"
                if len(suggested_fields) > 1
                else "Add a list_display tuple naming the fields most useful to see at a glance."
            )
            diagnostics.append(_diag(
                filepath, app_name, model_name, admin_node,
                message=f"{admin_node.name} has no list_display configuration",
                explanation=(
                    f"Without list_display, the admin list view for {model_name} falls back to showing "
                    "just each row's __str__(), which is harder to scan and can't be sorted per-column."
                ),
                suggested_fix=suggested_fix,
            ))

        if fields_with_types is None:
            fields_with_types = _find_model_fields(app_path, model_name)

        if not _has_attr(admin_node, "list_filter"):
            filter_candidates = [
                name for name, ftype, _auto, _rel, has_choices in fields_with_types
                if ftype in _LIST_FILTER_CANDIDATE_TYPES or has_choices
            ]
            if filter_candidates:
                chosen = filter_candidates[:4]
                diagnostics.append(_diag(
                    filepath, app_name, model_name, admin_node,
                    message=f"{admin_node.name} has no list_filter configuration",
                    explanation=(
                        f"{model_name} has boolean/relational/choice field(s) ({', '.join(chosen)}) — "
                        "common, high-value list_filter candidates for narrowing down the admin list view."
                    ),
                    suggested_fix=f"list_filter = {_format_str_tuple(chosen)}",
                ))

        if not _has_attr(admin_node, "search_fields"):
            search_candidates = [
                name for name, ftype, _auto, _rel, _choices in fields_with_types if ftype in _SEARCH_FIELD_TYPES
            ]
            if search_candidates:
                chosen = search_candidates[:4]
                diagnostics.append(_diag(
                    filepath, app_name, model_name, admin_node,
                    message=f"{admin_node.name} has no search_fields configuration",
                    explanation=(
                        f"{model_name} has text field(s) ({', '.join(chosen)}) that are natural "
                        "search_fields candidates — without search_fields, the admin's search box "
                        "does nothing at all."
                    ),
                    suggested_fix=f"search_fields = {_format_str_tuple(chosen)}",
                ))

        if not _has_attr(admin_node, "date_hierarchy"):
            date_candidates = [
                name for name, ftype, _auto, _rel, _choices in fields_with_types if ftype in _DATE_FIELD_TYPES
            ]
            if date_candidates:
                chosen_field = date_candidates[0]
                diagnostics.append(_diag(
                    filepath, app_name, model_name, admin_node,
                    message=f"{admin_node.name} has no date_hierarchy configuration",
                    explanation=(
                        f"{model_name} has a date/datetime field ('{chosen_field}') — date_hierarchy "
                        "adds convenient date-based drilldown navigation above the admin list."
                    ),
                    suggested_fix=f"date_hierarchy = {chosen_field!r}",
                ))

        if not _has_attr(admin_node, "readonly_fields"):
            auto_fields = [name for name, _ftype, is_auto, _rel, _choices in fields_with_types if is_auto]
            if auto_fields:
                chosen = auto_fields[:4]
                diagnostics.append(_diag(
                    filepath, app_name, model_name, admin_node,
                    message=f"{admin_node.name} has no readonly_fields configuration",
                    explanation=(
                        f"{model_name} has auto-managed timestamp field(s) ({', '.join(chosen)}) "
                        "(auto_now/auto_now_add) that can't be meaningfully edited — marking them "
                        "readonly_fields avoids a confusing, silently-ignored edit in the admin form."
                    ),
                    suggested_fix=f"readonly_fields = {_format_str_tuple(chosen)}",
                ))

        if not _has_attr(admin_node, "autocomplete_fields"):
            autocomplete_candidates = [
                name for name, ftype, _auto, rel, _choices in fields_with_types
                if ftype in _RELATIONAL_FIELD_TYPES and rel is not None and rel in registered_model_names
            ]
            if autocomplete_candidates:
                chosen = autocomplete_candidates[:4]
                diagnostics.append(_diag(
                    filepath, app_name, model_name, admin_node,
                    message=f"{admin_node.name} has no autocomplete_fields configuration",
                    explanation=(
                        f"{model_name} has relational field(s) ({', '.join(chosen)}) whose related "
                        "model has its own admin registered elsewhere in this project — "
                        "autocomplete_fields replaces a slow, load-everything dropdown with a "
                        "searchable widget backed by that model's search_fields."
                    ),
                    suggested_fix=f"autocomplete_fields = {_format_str_tuple(chosen)}",
                ))

    return diagnostics


def _find_registered_model_names(apps):
    """Collects registered model names across EVERY app's admin.py, not
    just one file — this is what makes the autocomplete_fields check
    possible: a field's related model might be registered in a
    completely different app than the field itself lives in."""
    names = set()
    for _app_name, app_path in apps:
        admin_file = os.path.join(app_path, "admin.py")
        if not os.path.isfile(admin_file):
            continue
        try:
            with open(admin_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=admin_file)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for _admin_node, model_name in _find_admin_registrations(tree):
            names.add(model_name)
    return names


def scan_admin(apps):
    registered_model_names = _find_registered_model_names(apps)
    diagnostics = []
    for app_name, app_path in apps:
        admin_file = os.path.join(app_path, "admin.py")
        if os.path.isfile(admin_file):
            diagnostics.extend(analyze_admin_file(app_name, app_path, admin_file, registered_model_names))
    return diagnostics
