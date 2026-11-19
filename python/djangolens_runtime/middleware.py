"""Opt-in Django middleware: captures every SQL query issued during a
request and writes one JSON report per request to disk for
`django-doctor runtime-report` to read.

NOTE ON VERIFICATION: same caveat as instrumentation.py — this requires
Django, which is not installed in the environment that built this file.
Written directly against documented Django middleware and settings APIs,
but unverified until run against a real project.

Enable it in your Django project's settings:

    MIDDLEWARE = [
        # ... your other middleware ...
        "djangolens_runtime.middleware.DjangoLensQueryMiddleware",
    ]

    DJANGOLENS_RUNTIME = {
        "enabled": True,  # opt-in; does nothing unless explicitly True
        "output_dir": BASE_DIR / ".djangolens" / "runtime",
    }

Then read the reports with DjangoLens itself:

    django-doctor runtime-report /path/to/.djangolens/runtime

Privacy: everything stays on disk, locally, under `output_dir`. Nothing
is sent over the network. Only SQL text, timing, and source location are
recorded — not request bodies, headers, cookies, or query results. Turn
this off in production (`enabled: False`, or don't add the middleware at
all) — it adds per-query overhead and writes a file per request.
"""

import json
import os
import time

from django.conf import settings

from .instrumentation import QueryCollector
from .query_analysis import analyze_events


class DjangoLensQueryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        config = getattr(settings, "DJANGOLENS_RUNTIME", {}) or {}
        self.enabled = bool(config.get("enabled", False))
        self.output_dir = str(config.get("output_dir", ".djangolens/runtime"))
        self.project_root = config.get("project_root") or str(getattr(settings, "BASE_DIR", ""))

    def __call__(self, request):
        if not self.enabled:
            return self.get_response(request)

        start = time.perf_counter()
        with QueryCollector(project_root=self.project_root) as collector:
            response = self.get_response(request)
        wall_time = time.perf_counter() - start

        analysis = analyze_events(collector.events)
        report = {
            "path": request.path,
            "method": request.method,
            "wall_time": wall_time,
            **analysis,
        }
        self._write_report(report)

        return response

    def _write_report(self, report):
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            filename = f"{int(time.time() * 1000)}.json"
            with open(os.path.join(self.output_dir, filename), "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
        except OSError:
            pass  # a reporting failure must never break the actual request
