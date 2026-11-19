"""DJANGO005: Signal Detector (static).

Detects two common Django signal footguns via `ast`:

1. Recursive signal risk: a `post_save`/`pre_save` receiver that calls
   `instance.save()` on the same instance it received. This is a common way
   to accidentally create an infinite save -> signal -> save loop.

2. Synchronous external side effects: a receiver that makes a network call
   (`requests`/`httpx`/`urllib3` .get/.post/.put/.patch/.delete/.request) or
   sends email (`send_mail`) directly in the handler body. Since signal
   receivers run synchronously as part of the triggering `save()`/
   `delete()` call, this blocks the caller on an external service.

Two registration styles are both recognized:

- Decorator: `@receiver(post_save, sender=Order)` on the handler function.
- Imperative: `post_save.connect(handler, sender=Order)` anywhere in the
  module, where `handler` is either a reference to a function defined
  elsewhere in the same file, or an inline `lambda`.

Both are `heuristic` confidence: this is pattern matching on syntax, not
proof the code will actually recurse or block, since Django's actual
runtime behavior depends on things (guards, conditional branches,
`dispatch_uid`) that static analysis can't fully evaluate.
"""

import ast

_NETWORK_MODULE_NAMES = {"requests", "httpx", "urllib3"}
_NETWORK_METHOD_NAMES = {"get", "post", "put", "patch", "delete", "request"}
_RECURSION_PRONE_SIGNALS = {"post_save", "pre_save"}
_KNOWN_SIGNAL_NAMES = {
    "post_save", "pre_save", "post_delete", "pre_delete", "m2m_changed",
    "pre_init", "post_init",
}


def _name_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _receiver_signal_info(decorator):
    """For `@receiver(post_save, sender=Order)` returns ('post_save', 'Order').
    Returns None if this decorator isn't a `receiver(...)` call."""
    if not isinstance(decorator, ast.Call):
        return None
    if _name_of(decorator.func) != "receiver":
        return None
    if not decorator.args:
        return None

    signal_name = _name_of(decorator.args[0])
    sender = None
    for kw in decorator.keywords:
        if kw.arg == "sender":
            sender = _name_of(kw.value)
    return signal_name, sender


def _find_connect_registrations(tree):
    """Finds `<signal>.connect(handler, sender=Model)` calls anywhere in the
    module. Returns a list of (signal_name, handler_node, sender), where
    handler_node is the AST node passed as the first positional argument —
    typically an ast.Name referencing a function defined elsewhere in the
    file, or an inline ast.Lambda."""
    registrations = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
        ):
            continue
        signal_name = _name_of(node.func.value)
        if signal_name not in _KNOWN_SIGNAL_NAMES or not node.args:
            continue
        handler_node = node.args[0]
        sender = None
        for kw in node.keywords:
            if kw.arg == "sender":
                sender = _name_of(kw.value)
        registrations.append((signal_name, handler_node, sender))
    return registrations


def _instance_param_name(node):
    """Django signal receivers conventionally name the affected object
    `instance` (post_save, pre_save, post_delete, pre_delete all do this).
    Works for FunctionDef/AsyncFunctionDef/Lambda alike — all expose
    `.args.args`. If the function doesn't use that name, we intentionally
    don't guess — better to miss the check than misidentify an unrelated
    parameter."""
    for arg in node.args.args:
        if arg.arg == "instance":
            return "instance"
    return None


def _find_self_save_calls(node, instance_param):
    calls = []
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "save"
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == instance_param
        ):
            calls.append(sub)
    return calls


def _find_external_calls(node):
    """Returns a list of (kind, call_node) for network calls and email
    sends found anywhere in the receiver body (function or lambda)."""
    found = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in _NETWORK_MODULE_NAMES
            and func.attr in _NETWORK_METHOD_NAMES
        ):
            found.append(("network", sub))
        elif isinstance(func, ast.Name) and func.id == "send_mail":
            found.append(("email", sub))
    return found


def _diag(filepath, node, severity, message, explanation, suggested_fix):
    return {
        "rule_id": "DJANGO005",
        "analyzer": "signals",
        "severity": severity,
        "confidence": "heuristic",
        "message": message,
        "explanation": explanation,
        "suggested_fix": suggested_fix,
        "file": filepath,
        "line": node.lineno,
        "col": node.col_offset,
    }


def _check_receiver(node, signal_name, sender, filepath, label):
    """Shared checks for a receiver body. `node` can be a FunctionDef,
    AsyncFunctionDef, or Lambda — all three expose `.args.args` and are
    walkable with `ast.walk`, so the same logic covers both `@receiver`-
    decorated functions and handlers passed to `.connect(...)`."""
    diagnostics = []
    sender_label = f" (sender={sender})" if sender else ""

    if signal_name in _RECURSION_PRONE_SIGNALS:
        instance_param = _instance_param_name(node)
        if instance_param:
            self_save_calls = _find_self_save_calls(node, instance_param)
            if self_save_calls:
                diagnostics.append(_diag(
                    filepath, self_save_calls[0],
                    severity="warning",
                    message=f"Possible recursive signal: {label}{sender_label} calls {instance_param}.save()",
                    explanation=(
                        f"This is a {signal_name} receiver that calls .save() on the same instance "
                        f"it received. Since {signal_name} fires again on that save(), this can loop "
                        "unless something (a guard flag, update_fields check, etc.) prevents it."
                    ),
                    suggested_fix=(
                        "If a re-save is genuinely needed, guard it (e.g. check whether the relevant "
                        "field already has the target value before saving), or use "
                        "`update_fields=[...]` combined with a check inside the receiver to skip "
                        "re-processing, or move the logic into the model's save() method instead."
                    ),
                ))

    for kind, call_node in _find_external_calls(node):
        if kind == "network":
            diagnostics.append(_diag(
                filepath, call_node,
                severity="warning",
                message=f"Signal receiver '{label}'{sender_label} makes a synchronous network call",
                explanation=(
                    "Signal receivers run synchronously as part of the triggering save()/delete() "
                    "call. A network call here blocks the caller until the external request "
                    "completes, and a failure there can fail the save/delete."
                ),
                suggested_fix=(
                    "Consider offloading this to a background task queue (e.g. Celery) so the "
                    "triggering request isn't blocked on (or coupled to the reliability of) the "
                    "external service."
                ),
            ))
        elif kind == "email":
            diagnostics.append(_diag(
                filepath, call_node,
                severity="warning",
                message=f"Signal receiver '{label}'{sender_label} sends email synchronously",
                explanation=(
                    "send_mail() here runs synchronously inside the receiver, which runs "
                    "synchronously inside the triggering save()/delete() call."
                ),
                suggested_fix="Consider sending email via a background task queue instead.",
            ))

    return diagnostics


def scan_file_for_signal_risks(filepath):
    diagnostics = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return diagnostics

    function_defs = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        function_defs[node.name] = node

        signal_info = None
        for decorator in node.decorator_list:
            info = _receiver_signal_info(decorator)
            if info:
                signal_info = info
                break
        if signal_info is not None:
            signal_name, sender = signal_info
            diagnostics.extend(_check_receiver(node, signal_name, sender, filepath, label=node.name))

    for signal_name, handler_node, sender in _find_connect_registrations(tree):
        if isinstance(handler_node, ast.Name):
            target = function_defs.get(handler_node.id)
            if target is not None:
                diagnostics.extend(_check_receiver(target, signal_name, sender, filepath, label=target.name))
        elif isinstance(handler_node, ast.Lambda):
            diagnostics.extend(_check_receiver(handler_node, signal_name, sender, filepath, label="<lambda>"))

    return diagnostics


def scan_signals(apps, iter_python_files):
    """`iter_python_files` is injected (rather than imported) to avoid a
    circular import with indexer.py, which already exposes this walker."""
    diagnostics = []
    for _app_name, app_path in apps:
        for filepath in iter_python_files(app_path):
            diagnostics.extend(scan_file_for_signal_risks(filepath))
    return diagnostics


def _receiver_effects(node, signal_name):
    """Returns a subset of {"recursive_save", "network_call", "email"} —
    the same risk categories `_check_receiver` turns into diagnostics,
    but here as plain labels for chain visualization rather than
    severity-graded messages."""
    effects = []

    if signal_name in _RECURSION_PRONE_SIGNALS:
        instance_param = _instance_param_name(node)
        if instance_param and _find_self_save_calls(node, instance_param):
            effects.append("recursive_save")

    for kind, _call_node in _find_external_calls(node):
        if kind == "network" and "network_call" not in effects:
            effects.append("network_call")
        elif kind == "email" and "email" not in effects:
            effects.append("email")

    return effects


def find_signal_chains(filepath):
    """Returns a list of {signal, sender, receiver, file, line, effects}
    — one entry per receiver found in `filepath`, covering both
    `@receiver`-decorated and `.connect()`-registered handlers. This is
    the data model for signal chain visualization: unlike
    `scan_file_for_signal_risks()`, which only reports risky receivers as
    diagnostics, this returns EVERY receiver (risky or not), so a caller
    can render the full sender -> signal -> receiver picture and
    highlight the risky ones rather than only seeing what's flagged.
    """
    chains = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return chains

    function_defs = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        function_defs[node.name] = node

        for decorator in node.decorator_list:
            info = _receiver_signal_info(decorator)
            if info:
                signal_name, sender = info
                chains.append({
                    "signal": signal_name,
                    "sender": sender,
                    "receiver": node.name,
                    "file": filepath,
                    "line": node.lineno,
                    "effects": _receiver_effects(node, signal_name),
                })
                break  # one @receiver decorator per function is the norm

    for signal_name, handler_node, sender in _find_connect_registrations(tree):
        if isinstance(handler_node, ast.Name):
            target = function_defs.get(handler_node.id)
            if target is not None:
                chains.append({
                    "signal": signal_name,
                    "sender": sender,
                    "receiver": target.name,
                    "file": filepath,
                    "line": target.lineno,
                    "effects": _receiver_effects(target, signal_name),
                })
        elif isinstance(handler_node, ast.Lambda):
            chains.append({
                "signal": signal_name,
                "sender": sender,
                "receiver": "<lambda>",
                "file": filepath,
                "line": handler_node.lineno,
                "effects": _receiver_effects(handler_node, signal_name),
            })

    return chains


def scan_signal_chains(apps, iter_python_files):
    chains = []
    for _app_name, app_path in apps:
        for filepath in iter_python_files(app_path):
            chains.extend(find_signal_chains(filepath))
    return chains


def _models_saved_inside(node):
    """Best-effort: names of objects on which .save() is called inside a
    receiver body. Used to approximate multi-hop chains.
    Returns a set of variable names (e.g. {'instance', 'profile'}).
    """
    saved = set()
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "save"
            and isinstance(sub.func.value, ast.Name)
        ):
            saved.add(sub.func.value.id)
    return saved


def build_multi_hop_chains(apps, iter_python_files):
    """Attempt multi-hop signal chain detection.

    Strategy (static, best-effort):
    1. Collect every receiver with its sender model and effects.
    2. If a receiver body calls `.save()` on a name other than the incoming
       `instance`, treat that as a potential downstream model save.
    3. If a receiver for sender=X has recursive_save, link it to other
       post_save/pre_save receivers for the same sender as a self-loop hop.
    4. If we can resolve a constructed model name (e.g. Profile(...).save()
       or profile.save() where profile was assigned from Profile(...)),
       attach downstream receivers for that model.

    Returns a list of chain dicts with an extra `hops` field:
      hops: [{signal, sender, receiver, file, line, effects}, ...]
    Confidence remains heuristic — resolving the target model of an
    arbitrary .save() call is undecidable in general.
    """
    # Flatten one-hop chains first
    one_hop = scan_signal_chains(apps, iter_python_files)

    # Index receivers by sender model name
    by_sender = {}
    for c in one_hop:
        sender = c.get("sender") or ""
        by_sender.setdefault(sender, []).append(c)

    multi = []
    for c in one_hop:
        hops = [dict(c)]  # start with self
        effects = set(c.get("effects") or [])

        # Self-loop: recursive_save on same sender
        if "recursive_save" in effects and c.get("sender"):
            for other in by_sender.get(c["sender"], []):
                if other is c:
                    continue
                if other.get("signal") in _RECURSION_PRONE_SIGNALS:
                    hops.append({
                        "signal": other.get("signal"),
                        "sender": other.get("sender"),
                        "receiver": other.get("receiver"),
                        "file": other.get("file"),
                        "line": other.get("line"),
                        "effects": other.get("effects") or [],
                        "reason": "same_sender_after_resave",
                    })

        # Cross-model: look inside the receiver source for other Model(...).save()
        # We re-parse the file to find construction patterns.
        filepath = c.get("file")
        if filepath and c.get("receiver") and c["receiver"] != "<lambda>":
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=filepath)
            except (SyntaxError, UnicodeDecodeError, OSError):
                tree = None

            if tree is not None:
                # Find the function body for this receiver
                target_fn = None
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == c["receiver"]:
                        target_fn = node
                        break
                if target_fn is not None:
                    # Look for assignments like `profile = Profile(...)` followed by profile.save()
                    # or direct Profile(...).save() — extract the class name.
                    constructed = set()
                    for sub in ast.walk(target_fn):
                        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                            # Profile(...)
                            constructed.add(sub.func.id)
                        if (
                            isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "save"
                        ):
                            # Something.save() — if Something is a Name that was assigned
                            # from a Call to a class, we already captured the class above.
                            pass

                    for model_name in constructed:
                        if model_name == c.get("sender"):
                            continue
                        for other in by_sender.get(model_name, []):
                            hops.append({
                                "signal": other.get("signal"),
                                "sender": other.get("sender"),
                                "receiver": other.get("receiver"),
                                "file": other.get("file"),
                                "line": other.get("line"),
                                "effects": other.get("effects") or [],
                                "reason": f"save_of_{model_name}",
                            })

        entry = dict(c)
        entry["hops"] = hops
        entry["hop_count"] = len(hops)
        multi.append(entry)

    return multi
