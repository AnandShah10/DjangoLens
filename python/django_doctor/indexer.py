"""Builds a lightweight cross-app import graph using Python's ast module.

No project code is ever executed. Files that fail to parse (syntax errors,
encoding issues) are skipped rather than crashing the scan — a broken file
elsewhere in the repo should never take down the whole analysis.
"""

import ast
import os

_SKIP_DIRS = {"migrations", "__pycache__", "node_modules", ".venv", "venv"}


def iter_python_files(app_path):
    for dirpath, dirnames, filenames in os.walk(app_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _resolve_app_from_module(module_name, app_names):
    if not module_name:
        return None
    top = module_name.split(".")[0]
    return top if top in app_names else None


class ImportRecord:
    __slots__ = ("importing_app", "imported_app", "module", "file", "line", "col")

    def __init__(self, importing_app, imported_app, module, file, line, col):
        self.importing_app = importing_app
        self.imported_app = imported_app
        self.module = module
        self.file = file
        self.line = line
        self.col = col

    def to_dict(self):
        return {
            "importing_app": self.importing_app,
            "imported_app": self.imported_app,
            "module": self.module,
            "file": self.file,
            "line": self.line,
            "col": self.col,
        }


def build_cross_app_imports(project_root, apps):
    """Returns a list of ImportRecord for every import statement in `apps`
    that targets a *different* known app. Same-app imports are not
    interesting for architecture analysis and are skipped.
    """
    app_names = {name for name, _ in apps}
    records = []

    for app_name, app_path in apps:
        for filepath in iter_python_files(app_path):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=filepath)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    target_app = _resolve_app_from_module(node.module, app_names)
                    if target_app and target_app != app_name:
                        records.append(ImportRecord(
                            app_name, target_app, node.module, filepath, node.lineno, node.col_offset
                        ))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        target_app = _resolve_app_from_module(alias.name, app_names)
                        if target_app and target_app != app_name:
                            records.append(ImportRecord(
                                app_name, target_app, alias.name, filepath, node.lineno, node.col_offset
                            ))

    return records
