"""DJANGO004: Migration Risk Analyzer (static).

Parses Django migration files (`<app>/migrations/000N_*.py`) via `ast` and
flags operations that are commonly risky in production: destructive
field/model removal, irreversible data migrations, non-nullable fields
added without a default, and index additions that can lock large tables.

This is STATIC analysis only — there is no database connection, so it can
never know actual table size or row counts. No migration code is ever
executed; operations are identified purely from the AST shape of the
`operations = [...]` list.

Field-nullability tracking
---------------------------
`scan_migrations()` processes each app's migration files in dependency
order — computed from each file's actual `dependencies = [...]` list, not
filename numbering (see `_ordered_migration_filenames`) — and maintains a
running `{(model, field): nullable}` map as it sees `CreateModel`/
`AddField`/`AlterField` operations. This means an `AlterField(null=False)` is only
flagged as a real regression when we can see the field was previously
`null=True`; if we can see it was already non-nullable, it's not flagged
at all (nothing changed); if we truly can't determine the prior state
(e.g. the field predates a squashed migration, or `analyze_migration_file`
is called on a single file in isolation), it's still flagged, but at
`heuristic` confidence with a message that says so explicitly — see
`_check_alter_field` below.
"""

import ast
import os

_DESTRUCTIVE_OPS = {"RemoveField", "DeleteModel", "RemoveIndex", "RemoveConstraint"}

_SEVERITY_BY_RISK = {"high": "error", "medium": "warning", "low": "information"}


def _migration_files(apps):
    """Returns (app_name, migrations_dir, [migration_filenames_in_dependency_order]) per app.

    Ordering is computed from each file's actual `dependencies = [...]`
    list (a topological sort restricted to same-app edges — cross-app
    dependencies don't affect the within-app field-history ordering this
    is used for), not from filename numbering. Falls back to filename
    order only if the dependency graph doesn't fully resolve (a cycle, or
    a file whose dependencies couldn't be parsed) — see
    `_ordered_migration_filenames`.
    """
    result = []
    for app_name, app_path in apps:
        migrations_dir = os.path.join(app_path, "migrations")
        if not os.path.isdir(migrations_dir):
            continue
        filenames = [
            f for f in os.listdir(migrations_dir)
            if f.endswith(".py") and f != "__init__.py"
        ]
        ordered = _ordered_migration_filenames(app_name, migrations_dir, filenames)
        result.append((app_name, migrations_dir, ordered))
    return result


def _extract_dependencies(tree):
    """Returns [(app_label, migration_name), ...] from `dependencies =
    [...]` in the Migration class. Entries that aren't a literal
    `(str, str)` tuple (e.g. `migrations.swappable_dependency(...)`) are
    skipped rather than guessed at."""
    deps = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id == "dependencies":
                            if isinstance(stmt.value, ast.List):
                                for elt in stmt.value.elts:
                                    if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                                        app_label = _string_value(elt.elts[0])
                                        mig_name = _string_value(elt.elts[1])
                                        if app_label and mig_name:
                                            deps.append((app_label, mig_name))
    return deps


def _ordered_migration_filenames(app_name, migrations_dir, filenames):
    """Topologically sorts `filenames` using each file's same-app
    `dependencies` entries (Kahn's algorithm). Ties are broken by filename
    for determinism. Falls back to plain filename order if the graph
    doesn't fully resolve — e.g. a dependency pointing at a migration name
    that isn't in this directory (a squashed/replaced migration), or
    (which shouldn't happen in valid Django migrations, but this code
    doesn't trust that blindly) a cycle.
    """
    name_to_filename = {os.path.splitext(f)[0]: f for f in filenames}
    deps_by_name = {}

    for name, filename in name_to_filename.items():
        filepath = os.path.join(migrations_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except (SyntaxError, UnicodeDecodeError, OSError):
            deps_by_name[name] = []
            continue
        deps_by_name[name] = [
            mig for (app_label, mig) in _extract_dependencies(tree) if app_label == app_name
        ]

    indegree = {name: 0 for name in name_to_filename}
    graph = {name: [] for name in name_to_filename}
    for name, deps in deps_by_name.items():
        for dep in deps:
            if dep in name_to_filename:
                graph[dep].append(name)
                indegree[name] += 1
            # A dependency outside this directory (e.g. a squashed
            # migration) simply doesn't constrain ordering here.

    queue = sorted(n for n, d in indegree.items() if d == 0)
    ordered_names = []
    while queue:
        queue.sort()
        current = queue.pop(0)
        ordered_names.append(current)
        for nxt in graph[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if len(ordered_names) != len(name_to_filename):
        # Didn't fully resolve (cycle, or a name that never reached
        # indegree 0) — safer to fall back than to silently drop files.
        return sorted(filenames)

    return [name_to_filename[n] for n in ordered_names]


def _call_name(node):
    """For `migrations.AddIndex(...)` returns 'AddIndex'."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _get_kwarg(call_node, name):
    for kw in call_node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _string_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_field_kwarg(op, name):
    """AddField/AlterField pass `field=models.SomeField(null=..., default=...)`
    — the field's own options live on that nested call, not on the outer
    AddField/AlterField call itself."""
    field_kw = _get_kwarg(op, "field")
    if not isinstance(field_kw, ast.Call):
        return None
    return _get_kwarg(field_kw, name)


def _field_nullable(field_call):
    """Django's own default is null=False when the kwarg is absent, so
    absence is a definite (not unknown) False — this only returns None
    when field_call itself isn't a recognizable field constructor call."""
    if not isinstance(field_call, ast.Call):
        return None
    null_kw = _get_kwarg(field_call, "null")
    return isinstance(null_kw, ast.Constant) and null_kw.value is True


def _find_operations_list(tree):
    """Finds the `operations = [...]` assignment inside `class
    Migration(migrations.Migration)`. Returns [] (not an error) if the
    file doesn't have the expected shape — a hand-written or unusual
    migration file should never crash the scan."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id == "operations":
                            if isinstance(stmt.value, ast.List):
                                return stmt.value.elts
    return []


def _diag(filepath, op_node, app_name, message, explanation, suggested_fix, risk, confidence="static"):
    return {
        "rule_id": "DJANGO004",
        "analyzer": "migrations",
        "severity": _SEVERITY_BY_RISK[risk],
        "confidence": confidence,
        "message": message,
        "explanation": explanation,
        "suggested_fix": suggested_fix,
        "file": filepath,
        "line": getattr(op_node, "lineno", 1),
        "col": getattr(op_node, "col_offset", 0),
        "app": app_name,
    }


def _track_create_model(op, field_state):
    model_name = _string_value(_get_kwarg(op, "name"))
    fields_kw = _get_kwarg(op, "fields")
    if model_name is None or not isinstance(fields_kw, ast.List):
        return
    for elt in fields_kw.elts:
        if not (isinstance(elt, ast.Tuple) and len(elt.elts) == 2):
            continue
        field_name = _string_value(elt.elts[0])
        field_call = elt.elts[1]
        if field_name is not None:
            field_state[(model_name.lower(), field_name)] = _field_nullable(field_call)


def _track_add_field(op, field_state):
    model_name = _string_value(_get_kwarg(op, "model_name"))
    field_name = _string_value(_get_kwarg(op, "name"))
    field_call = _get_kwarg(op, "field")
    if model_name is not None and field_name is not None:
        field_state[(model_name.lower(), field_name)] = _field_nullable(field_call)


def _check_alter_field(op, app_name, filepath, field_state):
    diagnostics = []
    model_name = _string_value(_get_kwarg(op, "model_name"))
    field_name = _string_value(_get_kwarg(op, "name"))
    field_call = _get_kwarg(op, "field")
    new_nullable = _field_nullable(field_call)

    key = (model_name.lower(), field_name) if model_name and field_name else None
    old_nullable = field_state.get(key) if key is not None else None

    if new_nullable is False:
        if old_nullable is True:
            diagnostics.append(_diag(
                filepath, op, app_name,
                message=f"AlterField changes '{field_name}' from nullable to non-nullable",
                explanation=(
                    "An earlier migration in this app shows this field as null=True. If existing rows "
                    "have NULL in this column, this migration will fail until they're backfilled."
                ),
                suggested_fix="Backfill NULLs with a data migration before this AlterField, or keep null=True.",
                risk="medium",
                confidence="static",
            ))
        elif old_nullable is None:
            diagnostics.append(_diag(
                filepath, op, app_name,
                message=f"AlterField sets null=False on '{field_name}' (previous nullability unknown)",
                explanation=(
                    "DjangoLens could not determine whether this field allowed NULL in an earlier "
                    "migration within this app — it may predate the visible migration history (e.g. a "
                    "squashed migration) or the field/model name couldn't be resolved statically."
                ),
                suggested_fix="Confirm no existing rows have NULL in this column before deploying, or add null=True.",
                risk="medium",
                confidence="heuristic",
            ))
        # else old_nullable is False: the field was already required, so this
        # AlterField isn't a nullability regression — nothing to flag.

    if key is not None:
        field_state[key] = new_nullable

    return diagnostics


def analyze_migration_file(app_name, filepath, field_state=None):
    """Analyzes a single migration file. `field_state` is a
    `{(model, field): nullable}` dict that is read from and updated in
    place — pass the same dict across an app's ordered migration files
    (as `scan_migrations` does) to get history-aware AlterField checks.
    Omit it (or call this directly on one file) and every field looks
    like it has no known history, which is still handled explicitly —
    see `_check_alter_field`.
    """
    if field_state is None:
        field_state = {}

    diagnostics = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return diagnostics

    for op in _find_operations_list(tree):
        if not isinstance(op, ast.Call):
            continue
        op_name = _call_name(op)
        if op_name is None:
            continue

        if op_name == "CreateModel":
            _track_create_model(op, field_state)

        elif op_name in _DESTRUCTIVE_OPS:
            diagnostics.append(_diag(
                filepath, op, app_name,
                message=f"Destructive migration operation: {op_name}",
                explanation=(
                    f"{op_name} removes data-bearing schema. If this ships and is later rolled back, "
                    "the removed data/column/index cannot be recovered from the schema alone."
                ),
                suggested_fix="Confirm this is intentional and that a backup/rollback plan exists before deploying.",
                risk="high",
            ))

        elif op_name == "RunPython":
            reverse_code = _get_kwarg(op, "reverse_code")
            has_positional_reverse = len(op.args) >= 2
            if reverse_code is None and not has_positional_reverse:
                diagnostics.append(_diag(
                    filepath, op, app_name,
                    message="Irreversible RunPython migration (no reverse_code)",
                    explanation="This data migration has no reverse function, so `migrate <app> <previous>` cannot undo it.",
                    suggested_fix="Add a reverse_code function, or pass migrations.RunPython.noop if reversal genuinely isn't needed.",
                    risk="medium",
                ))
            diagnostics.append(_diag(
                filepath, op, app_name,
                message="RunPython migration: review for expensive operations on large tables",
                explanation=(
                    "RunPython can execute arbitrary Python against every row in a table. On large "
                    "tables this can be slow and, depending on how it's written, lock-inducing."
                ),
                suggested_fix="Batch the operation (process in chunks) if the target table may be large.",
                risk="low",
            ))

        elif op_name == "RunSQL":
            reverse_sql = _get_kwarg(op, "reverse_sql")
            has_positional_reverse = len(op.args) >= 2
            if reverse_sql is None and not has_positional_reverse:
                diagnostics.append(_diag(
                    filepath, op, app_name,
                    message="Irreversible RunSQL migration (no reverse_sql)",
                    explanation="This raw-SQL migration has no reverse_sql, so it can't be rolled back via Django's migration system.",
                    suggested_fix="Provide reverse_sql, or migrations.RunSQL.noop if reversal genuinely isn't needed.",
                    risk="medium",
                ))

        elif op_name == "AddField":
            null_kw = _get_field_kwarg(op, "null")
            default_kw = _get_field_kwarg(op, "default")
            is_nullable = isinstance(null_kw, ast.Constant) and null_kw.value is True
            has_default = default_kw is not None
            if not is_nullable and not has_default:
                diagnostics.append(_diag(
                    filepath, op, app_name,
                    message="AddField without null=True or a default",
                    explanation=(
                        "Adding a non-nullable field with no default will fail against a table that "
                        "already has rows — there's nothing to backfill existing rows with."
                    ),
                    suggested_fix="Add null=True, or provide a default= value.",
                    risk="high",
                ))
            _track_add_field(op, field_state)

        elif op_name == "AddIndex":
            diagnostics.append(_diag(
                filepath, op, app_name,
                message="Index addition: potentially expensive on a large table",
                explanation=(
                    "Adding an index can lock the table and take a long time on tables with many rows. "
                    "Table size can't be determined by static analysis."
                ),
                suggested_fix=(
                    "On PostgreSQL, consider CREATE INDEX CONCURRENTLY via RunSQL + "
                    "SeparateDatabaseAndState instead of the default AddIndex."
                ),
                risk="medium",
            ))

        elif op_name == "AlterField":
            diagnostics.extend(_check_alter_field(op, app_name, filepath, field_state))

    return diagnostics


def scan_migrations(apps):
    diagnostics = []
    for app_name, migrations_dir, filenames in _migration_files(apps):
        field_state = {}
        for filename in filenames:
            filepath = os.path.join(migrations_dir, filename)
            diagnostics.extend(analyze_migration_file(app_name, filepath, field_state))
    return diagnostics
