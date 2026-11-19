"""DJANGO016: Possibly-unregistered signal handlers (static).

Finds functions that look like Django signal receivers (signature includes
`sender` + `**kwargs` or `instance`) but are never wired via `@receiver(...)`
or `<signal>.connect(...)`.

Also finds `.connect(handler)` where `handler` is a Name that does not resolve
to any function in the same module (broken registration).

Confidence is heuristic — many legitimate helpers share similar signatures.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional, Set


def _name_of(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


_KNOWN_SIGNALS = {
    "post_save", "pre_save", "post_delete", "pre_delete", "m2m_changed",
    "pre_init", "post_init", "request_started", "request_finished",
}


def _looks_like_receiver_signature(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    arg_names = [a.arg for a in node.args.args]
    has_kwargs = node.args.kwarg is not None
    # Classic: (sender, instance, **kwargs) or (sender, **kwargs)
    if "sender" in arg_names and (has_kwargs or "instance" in arg_names):
        return True
    return False


def _is_receiver_decorated(node: ast.FunctionDef) -> bool:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and _name_of(dec.func) == "receiver":
            return True
        if _name_of(dec) == "receiver":
            return True
    return False


def scan_file_for_unused_signals(filepath: str) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return diagnostics

    function_defs = {}
    candidates: List[ast.FunctionDef] = []
    connected_names: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_defs[node.name] = node
            if _is_receiver_decorated(node):
                connected_names.add(node.name)
            elif _looks_like_receiver_signature(node):
                candidates.append(node)

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and _name_of(node.func.value) in _KNOWN_SIGNALS
            and node.args
        ):
            handler = node.args[0]
            if isinstance(handler, ast.Name):
                connected_names.add(handler.id)
            elif isinstance(handler, ast.Lambda):
                pass  # anonymous — can't be "unused function"

    for fn in candidates:
        if fn.name in connected_names:
            continue
        # Skip private helpers that just happen to have sender in signature
        if fn.name.startswith("_") and "signal" not in fn.name.lower() and "receiv" not in fn.name.lower():
            # still report but lower confidence path — we use heuristic either way
            pass
        diagnostics.append({
            "rule_id": "DJANGO016",
            "analyzer": "unused_signals",
            "severity": "information",
            "confidence": "heuristic",
            "message": f"Possible unregistered signal handler: {fn.name}",
            "explanation": (
                f"'{fn.name}' has a signal-receiver-like signature (sender / instance / **kwargs) "
                f"but is not decorated with @receiver(...) and is not passed to .connect() in this file."
            ),
            "suggested_fix": (
                "Wire it with @receiver(signal, sender=Model) or signal.connect(...), "
                "or rename/adjust the signature if it is not a signal handler."
            ),
            "file": filepath,
            "line": fn.lineno,
            "col": fn.col_offset,
            "handler": fn.name,
        })

    # Broken connect: Name not defined in this module
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            continue
        handler_name = node.args[0].id
        if handler_name not in function_defs:
            diagnostics.append({
                "rule_id": "DJANGO016",
                "analyzer": "unused_signals",
                "severity": "warning",
                "confidence": "heuristic",
                "message": f"signal.connect() references unknown handler '{handler_name}'",
                "explanation": (
                    f"No function named '{handler_name}' is defined in this module. "
                    f"The registration may be broken (typo or missing import)."
                ),
                "suggested_fix": "Define the handler in this module or import it before connecting.",
                "file": filepath,
                "line": node.lineno,
                "col": node.col_offset,
                "handler": handler_name,
            })

    return diagnostics


def scan_unused_signals(apps, iter_python_files) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    for _app_name, app_path in apps:
        for filepath in iter_python_files(app_path):
            diagnostics.extend(scan_file_for_unused_signals(filepath))
    return diagnostics
