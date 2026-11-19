"""Django project and app discovery.

This module deliberately does not assume `project_name == settings module
name`, and does not execute any project code. It only looks at the
filesystem shape (manage.py, apps.py, models.py) to decide what looks like
a Django project / app.
"""

import os

_IGNORED_DIRS = {
    "node_modules", ".venv", "venv", "env", ".git", "__pycache__",
    "build", "dist", ".mypy_cache", ".pytest_cache", "migrations",
    "static", "staticfiles", "templates", "media",
}

_MAX_CONTAINER_DEPTH = 3  # see discover_apps() docstring


def find_django_root(start_path):
    """Walk the tree under start_path looking for the nearest manage.py.

    Returns the directory containing manage.py, or None if no Django
    project signal was found. This is intentionally conservative: it does
    not guess based on directory names alone.
    """
    for dirpath, dirnames, filenames in os.walk(start_path):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        if "manage.py" in filenames:
            return dirpath
    return None


def _is_app_dir(full_path):
    has_models = (
        os.path.isfile(os.path.join(full_path, "models.py"))
        or os.path.isdir(os.path.join(full_path, "models"))
    )
    has_apps_py = os.path.isfile(os.path.join(full_path, "apps.py"))
    return has_models or has_apps_py


def _scan_for_apps(directory):
    """Returns (app_name, app_path) pairs for immediate subdirectories of
    `directory` that look like Django apps."""
    apps = []
    for entry in sorted(os.listdir(directory)):
        full = os.path.join(directory, entry)
        if not os.path.isdir(full) or entry in _IGNORED_DIRS or entry.startswith("."):
            continue
        if _is_app_dir(full):
            apps.append((entry, full))
    return apps


def _scan_for_apps_recursive(directory, max_depth):
    """Like `_scan_for_apps`, but also descends into subdirectories that
    aren't themselves apps, up to `max_depth` additional levels — for
    src-layout projects where apps sit under an extra package level
    (e.g. `src/myproject/orders/`, not just `src/orders/`).

    A directory that itself looks like an app is NOT descended into
    further (an app containing sub-apps is unusual enough that treating
    its subdirectories as separate apps would be more likely wrong than
    right — e.g. a `migrations/` or `management/` subfolder shouldn't
    spawn phantom "apps").
    """
    apps = []
    _recurse(directory, max_depth, apps)
    return apps


def _recurse(directory, depth_remaining, apps):
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return

    for entry in entries:
        full = os.path.join(directory, entry)
        if not os.path.isdir(full) or entry in _IGNORED_DIRS or entry.startswith("."):
            continue

        if _is_app_dir(full):
            apps.append((entry, full))
            continue  # don't also descend into a directory we just counted as an app

        if depth_remaining > 0:
            _recurse(full, depth_remaining - 1, apps)


def discover_apps(project_root, extra_containers=None):
    """Return a list of (app_name, app_path) for directories that look like
    Django apps: a directory containing models.py, a models/ package, or
    an apps.py (AppConfig).

    Covers the common flat layout, plus apps nested under a `src/` or
    `apps/` directory at the project root (both common conventions) —
    up to a few levels deep now, not just one, so
    `src/myproject/orders/models.py`-style extra package nesting is
    found too.

    `extra_containers` is an optional list of additional directory names
    (relative to project_root) to scan the same way as `src`/`apps`
    (from djangolens.config.json `discovery.extra_app_roots`).
    """
    if not project_root or not os.path.isdir(project_root):
        return []

    apps = _scan_for_apps(project_root)
    found_names = {name for name, _ in apps}

    containers = ["src", "apps"]
    if extra_containers:
        for c in extra_containers:
            if c and c not in containers:
                containers.append(c)

    for container_name in containers:
        container_path = os.path.join(project_root, container_name)
        if os.path.isdir(container_path):
            for name, path in _scan_for_apps_recursive(container_path, _MAX_CONTAINER_DEPTH):
                if name not in found_names:
                    apps.append((name, path))
                    found_names.add(name)

    return apps
