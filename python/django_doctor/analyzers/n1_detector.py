"""DJANGO003: heuristic detection of possible N+1 queries.

This is deliberately conservative and labeled "heuristic" confidence (never
"high") because it is pattern matching on syntax, not a real query planner:
it cannot know whether `orders` actually came from a queryset, or whether
the relation is already cached. See ORM Query Detective (deferred, runtime
analysis) for a ground-truth version of this signal.

Heuristic: a `for X in <expr>` loop where <expr> looks like a Django
queryset call chain (contains .objects/.all/.filter/.exclude), combined
with an attribute-of-an-attribute access on the loop variable inside the
loop body (`X.relation.field`) that isn't covered by a preceding
.select_related()/.prefetch_related() call on the same queryset.

A single-level access (`X.field`) is never flagged — only relationship
traversal, which is what actually risks a per-row query.

Each diagnostic also carries structured `relation`/`insert_line`/
`insert_col` fields (beyond the human-readable message) identifying a safe
place to append `.select_related(...)`/`.prefetch_related(...)` — see
`_check_loop_body` for why that position is always safe to edit.
"""

import ast

_QUERYSET_HINTS = {"all", "filter", "exclude", "objects"}
_OPTIMIZER_CALLS = {"select_related", "prefetch_related"}


def _call_chain_names(node):
    """Flattens a Call/Attribute/Name chain into an ordered list of names,
    e.g. Order.objects.filter(...) -> ['Order', 'objects', 'filter'].
    """
    names = []
    while isinstance(node, (ast.Call, ast.Attribute)):
        if isinstance(node, ast.Call):
            node = node.func
            continue
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
    return list(reversed(names))


def _looks_like_queryset(node):
    return any(name in _QUERYSET_HINTS for name in _call_chain_names(node))


def _has_optimizer(node):
    return any(name in _OPTIMIZER_CALLS for name in _call_chain_names(node))


def _relation_names_from_optimizer(node):
    """Best-effort extraction of string args passed to select_related /
    prefetch_related anywhere in the call chain, e.g.
    Order.objects.select_related("customer").all() -> {"customer"}.
    """
    relations = set()
    cur = node
    while isinstance(cur, ast.Call):
        func = cur.func
        if isinstance(func, ast.Attribute) and func.attr in _OPTIMIZER_CALLS:
            for arg in cur.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    relations.add(arg.value.split("__")[0])
        cur = func.value if isinstance(func, ast.Attribute) else None
    return relations


def _check_loop_body(body, loop_var, optimized_relations, diagnostics, filepath, insert_point):
    seen = set()
    for stmt in body:
        for sub in ast.walk(stmt):
            if not isinstance(sub, ast.Attribute) or not isinstance(sub.value, ast.Attribute):
                continue
            inner = sub.value
            if not (isinstance(inner.value, ast.Name) and inner.value.id == loop_var):
                continue
            relation = inner.attr
            if relation in optimized_relations or relation in seen:
                continue
            seen.add(relation)
            diagnostics.append({
                "rule_id": "DJANGO003",
                "analyzer": "orm",
                "severity": "warning",
                "confidence": "heuristic",
                "message": f"Possible N+1 query: '{loop_var}.{relation}' accessed inside a loop",
                "explanation": (
                    f"'{relation}' looks like a related-object access on '{loop_var}', which is "
                    "iterated from a queryset. Without select_related()/prefetch_related(), Django "
                    "will issue a separate query per iteration."
                ),
                "suggested_fix": (
                    f'Consider .select_related("{relation}") (for ForeignKey/OneToOne) or '
                    f'.prefetch_related("{relation}") (for reverse FK/ManyToMany) on the queryset.'
                ),
                "file": filepath,
                "line": sub.lineno,
                "col": sub.col_offset,
                # Structured fields (beyond the human-readable message above)
                # so a Code Action can offer a real quick fix without parsing
                # English text. `relation` is the field name; `insert_line`/
                # `insert_col` is always the position right after the END of
                # the whole queryset expression — appending
                # `.select_related("<relation>")` or
                # `.prefetch_related("<relation>")` there is syntactically
                # safe regardless of the chain's shape or how many lines it
                # spans, since both are valid QuerySet methods callable at
                # the end of any queryset chain. We deliberately do NOT pick
                # select_related vs prefetch_related here — that depends on
                # the relation's actual type (forward vs reverse/M2M), which
                # static analysis can't determine without executing the
                # Django app registry. The editor side offers both and lets
                # the developer pick the correct one.
                "relation": relation,
                "insert_line": insert_point[0],
                "insert_col": insert_point[1],
            })


_BLOCK_FIELDS = ("body", "orelse", "finalbody")


def _sub_statement_lists(stmt):
    """Statement lists to recurse into for compound statements other than
    For/FunctionDef/ClassDef (which are handled specially in _process_scope)."""
    lists = []
    for field in _BLOCK_FIELDS:
        val = getattr(stmt, field, None)
        if isinstance(val, list) and val and isinstance(val[0], ast.stmt):
            lists.append(val)
    for handler in getattr(stmt, "handlers", None) or []:
        lists.append(handler.body)
    return lists


def _process_scope(stmts, diagnostics, filepath, var_map):
    """Walks a list of statements in order, tracking simple `name = expr`
    assignments in `var_map` so that a loop like:

        orders = Order.objects.all()
        for order in orders:
            ...

    still resolves `orders` back to the queryset expression, not just the
    literal (and much rarer) `for order in Order.objects.all():` form.

    Each function/class body gets its own fresh var_map (a new scope) so
    variables don't leak across functions.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            var_map[stmt.targets[0].id] = stmt.value

        if isinstance(stmt, ast.For):
            loop_var = stmt.target.id if isinstance(stmt.target, ast.Name) else None
            iter_expr = stmt.iter
            if isinstance(iter_expr, ast.Name) and iter_expr.id in var_map:
                iter_expr = var_map[iter_expr.id]
            if loop_var and _looks_like_queryset(iter_expr):
                optimized = _relation_names_from_optimizer(iter_expr) if _has_optimizer(iter_expr) else set()
                insert_point = (
                    getattr(iter_expr, "end_lineno", iter_expr.lineno),
                    getattr(iter_expr, "end_col_offset", iter_expr.col_offset),
                )
                _check_loop_body(stmt.body, loop_var, optimized, diagnostics, filepath, insert_point)
            _process_scope(stmt.body, diagnostics, filepath, var_map)
            if stmt.orelse:
                _process_scope(stmt.orelse, diagnostics, filepath, var_map)
            continue

        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _process_scope(stmt.body, diagnostics, filepath, {})
            continue

        for block in _sub_statement_lists(stmt):
            _process_scope(block, diagnostics, filepath, var_map)


def detect_n_plus_one(tree, filepath):
    diagnostics = []
    _process_scope(tree.body, diagnostics, filepath, {})
    return diagnostics


def scan_file_for_n_plus_one(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    return detect_n_plus_one(tree, filepath)
