"""DjangoLens runtime companion package.

Install this into the Django project you want to profile (NOT into
DjangoLens' own environment) and enable it explicitly — see README.md in
this directory. It captures SQL queries per request and writes a JSON
report DjangoLens' `django-doctor runtime-report` command can read.

This package is split so that the parts requiring Django
(`instrumentation.py`, `middleware.py`) are separate from the parts that
don't (`query_analysis.py`, `frames.py`) — the latter are unit-tested
directly in DjangoLens' own test suite without needing Django installed.
Importing this top-level package does nothing by itself; nothing runs
until `middleware.py`'s `DjangoLensQueryMiddleware` is added to
MIDDLEWARE and explicitly enabled in settings.
"""
