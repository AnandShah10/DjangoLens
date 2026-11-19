"""SQL capture for one request, via Django's `connection.execute_wrapper`
(available since Django 3.0). This works regardless of `DEBUG` — unlike
`connection.queries`, which only populates when `DEBUG=True` or a cursor
is force-debugged.

NOTE ON VERIFICATION: this module requires Django to import, and Django
is not installed in the environment that built this file — it has NOT
been executed or tested here. It's written directly against Django's
documented `execute_wrapper` API (a callable taking
`(execute, sql, params, many, context)`, where `context` is a dict with
`"connection"` and `"cursor"` keys), but treat it as unverified until you
run it against a real Django project. `query_analysis.py` and
`frames.py` — the actual analysis logic — are Django-free and ARE tested.
"""

import time
import traceback

from django.db import connections

from .frames import first_project_frame


class QueryCollector:
    """Context manager that collects one event per SQL statement executed
    on any database connection while it's active, across all
    `DATABASES` aliases (not just "default").

    Usage:

        with QueryCollector(project_root=settings.BASE_DIR) as collector:
            response = get_response(request)
        events = collector.events
    """

    def __init__(self, project_root=None):
        self.project_root = str(project_root) if project_root else None
        self.events = []
        self._wrappers = []

    def _capture(self, execute, sql, params, many, context):
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            duration = time.perf_counter() - start
            stack = traceback.extract_stack()
            # Drop this frame and execute()'s own frame — neither is
            # useful as "where the query came from".
            frame = first_project_frame(stack[:-1], self.project_root)
            self.events.append({
                "sql": sql,
                "duration": duration,
                "alias": context["connection"].alias,
                "file": getattr(frame, "filename", None),
                "line": getattr(frame, "lineno", None),
                "function": getattr(frame, "name", None),
            })

    def __enter__(self):
        for connection in connections.all():
            wrapper = connection.execute_wrapper(self._capture)
            wrapper.__enter__()
            self._wrappers.append(wrapper)
        return self

    def __exit__(self, exc_type, exc_value, tb):
        for wrapper in reversed(self._wrappers):
            wrapper.__exit__(exc_type, exc_value, tb)
        self._wrappers = []
