"""DJANGO001 (circular app dependency) and the configurable architecture
linter (allow-list / deny-list rules between apps).
"""

from collections import defaultdict


def detect_circular_dependencies(import_records):
    """DFS-based cycle detection over the app import graph.

    Reports each cycle once, with high confidence — this is a fact about
    the import graph, not a heuristic. Attaches a representative file/line
    (the import statement for the cycle's first edge) so the diagnostic
    can render in an editor's Problems panel like any other — without a
    location it would only ever show up in the CLI/JSON output.
    """
    graph = defaultdict(set)
    edge_lookup = {}
    for rec in import_records:
        graph[rec.importing_app].add(rec.imported_app)
        key = (rec.importing_app, rec.imported_app)
        if key not in edge_lookup:
            edge_lookup[key] = rec

    diagnostics = []
    reported_cycles = set()
    globally_done = set()

    def dfs(node, path, path_set):
        if node in path_set:
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            key = frozenset(cycle)
            if key not in reported_cycles:
                reported_cycles.add(key)
                diag = {
                    "rule_id": "DJANGO001",
                    "analyzer": "architecture",
                    "severity": "error",
                    "confidence": "high",
                    "message": f"Circular dependency between apps: {' -> '.join(cycle)}",
                    "explanation": (
                        "These Django apps import from each other, forming a cycle. "
                        "This makes the apps hard to reuse, test, or reorganize independently."
                    ),
                    "suggested_fix": (
                        "Break the cycle by moving the shared code to a separate app/module, "
                        "or by inverting one of the dependencies (e.g. via signals or dependency injection)."
                    ),
                    "apps": cycle,
                }
                representative = edge_lookup.get((cycle[0], cycle[1]))
                if representative:
                    diag["file"] = representative.file
                    diag["line"] = representative.line
                    diag["col"] = representative.col
                diagnostics.append(diag)
            return
        if node in globally_done:
            return

        path.append(node)
        path_set.add(node)
        for neighbor in graph.get(node, ()):
            dfs(neighbor, path, path_set)
        path.pop()
        path_set.discard(node)
        globally_done.add(node)

    for app in list(graph.keys()):
        dfs(app, [], set())

    return diagnostics


def check_architecture_rules(import_records, config):
    """Applies user-configured allow-list (`canImport`) / deny-list
    (`cannotImport`) rules from djangolens.config.json.

    Config shape:
        {
          "architecture": {
            "orders":   {"canImport": ["users", "payments"]},
            "payments": {"cannotImport": ["orders"]}
          }
        }

    `canImport` is an allow-list: anything not listed is a violation.
    `cannotImport` is a deny-list: only listed apps are violations.
    An app with no rule configured is never flagged.
    """
    diagnostics = []
    rules = (config or {}).get("architecture", {})

    for rec in import_records:
        app_rules = rules.get(rec.importing_app)
        if not app_rules:
            continue

        can_import = app_rules.get("canImport")
        cannot_import = app_rules.get("cannotImport", [])

        violated = False
        reason = None
        if cannot_import and rec.imported_app in cannot_import:
            violated = True
            reason = f"'{rec.importing_app}' is configured to never import from '{rec.imported_app}'."
        elif can_import is not None and rec.imported_app not in can_import:
            violated = True
            reason = (
                f"'{rec.importing_app}' is only allowed to import from {can_import}, "
                f"not '{rec.imported_app}'."
            )

        if violated:
            diagnostics.append({
                "rule_id": "DJANGOARCH",
                "analyzer": "architecture",
                "severity": "error",
                "confidence": "high",
                "message": f"Architecture violation: {rec.importing_app} cannot depend on {rec.imported_app}",
                "explanation": reason,
                "suggested_fix": (
                    "Remove this import, or update djangolens.config.json if this dependency is intentional."
                ),
                "file": rec.file,
                "line": rec.line,
                "col": rec.col,
                "module": rec.module,
            })

    return diagnostics
