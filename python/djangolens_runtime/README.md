# djangolens_runtime

The runtime half of DjangoLens' "ORM Query Detective" feature: opt-in
middleware that captures every SQL query issued during a request, groups
duplicates (the runtime signature of an N+1 query) by source location, and
writes one JSON report per request to disk. `django-doctor runtime-report`
(part of DjangoLens' main CLI, in `python/django_doctor/`) reads and
summarizes those reports.

> **This package has not been run against a real Django project.** It was
> written directly against Django's documented `connection.execute_wrapper`
> and middleware APIs, but the environment that built it doesn't have
> Django installed, so `instrumentation.py` and `middleware.py` are
> unverified — read them before trusting them in anything that matters.
> `query_analysis.py` and `frames.py` (the actual query-grouping and
> source-mapping logic) have no Django dependency and ARE unit-tested —
> see DjangoLens' own `tests/test_query_analysis.py` and `tests/test_frames.py`.

## Install

This isn't published to PyPI. Copy (or symlink) this `djangolens_runtime/`
directory into your Django project so it's importable — e.g. drop it at
your project root, next to `manage.py`.

## Enable

In your Django project's settings:

```python
MIDDLEWARE = [
    # ... your other middleware ...
    "djangolens_runtime.middleware.DjangoLensQueryMiddleware",
]

DJANGOLENS_RUNTIME = {
    "enabled": True,  # opt-in — does nothing at all unless explicitly True
    "output_dir": str(BASE_DIR / ".djangolens" / "runtime"),
}
```

Then browse your app (or run whatever requests you want profiled). Each
request writes one `<timestamp>.json` file to `output_dir`.

## Read the results

From DjangoLens' `python/` directory:

```bash
python -m django_doctor.cli runtime-report /path/to/.djangolens/runtime
python -m django_doctor.cli runtime-report /path/to/.djangolens/runtime --format json
```

Output is sorted slowest-request-first, with each endpoint's single worst
duplicate-query group surfaced as a quick "what to fix first" signal.

## What it captures, and what it doesn't

Captured per query: the SQL text, execution time, database alias, and the
source location of the innermost call that isn't inside Django or a
third-party package (`frames.first_project_frame`).

**Not** captured: request bodies, headers, cookies, query *results*, or
anything else — only SQL text, timing, and where it came from.

**Not** sent anywhere. Reports are written to local disk only, under
`output_dir`. There is no network call anywhere in this package.

## Turn it off

Set `"enabled": False` (or remove the middleware entirely) before
deploying anywhere you don't want per-request overhead and a JSON file
written for every request — this is a profiling tool for local/staging
use, not something to leave on in production.

## Known limitations

- Requires Django 3.0+ (`connection.execute_wrapper` was added there).
- "Duplicate query" grouping (`query_analysis.analyze_events`) requires
  both the same normalized SQL **and** the same source file/line to
  match — the same query shape from two genuinely different call sites in
  one request is intentionally not flagged (see its docstring). This means
  an N+1 that fans out through more than one call site for the same loop
  won't be grouped as a single issue.
- No automatic cleanup of `output_dir` — it grows by one file per request
  for as long as this stays enabled.
