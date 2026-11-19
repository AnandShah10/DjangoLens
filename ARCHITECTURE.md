# DjangoLens — Architecture

## Layout

```
djangolens/
├── src/                        VS Code extension (TypeScript)
│   ├── extension.ts             activation, commands, diagnostics wiring
│   ├── pythonBridge.ts          spawns the Python analyzer, parses its JSON
│   ├── urlsTreeProvider.ts      "URLs" sidebar TreeDataProvider
│   ├── issuesTreeProvider.ts    "Issues" sidebar TreeDataProvider (health header + grouped by severity)
│   ├── codeActionProvider.ts    N+1 quick fix (select_related/prefetch_related)
│   ├── hoverProvider.ts         structured rule/confidence/explanation hover
│   ├── codeLensProvider.ts      inline lens above every flagged line
│   ├── settingsWebview.ts       "Settings Overview" webview panel
│   ├── runtimeReportWebview.ts  "Runtime Report" webview panel (reads djangolens_runtime JSON)
│   ├── apiDiffWebview.ts        "API Contract Drift" webview panel (prompts for a git ref)
│   └── signalChainsWebview.ts   "Signal Chains" webview panel (sender -> signal -> receiver -> effects)
│
├── resources/
│   └── icon.svg                 Activity Bar icon (monochrome, currentColor)
│
├── python/
│   ├── django_doctor/            static analysis engine + CLI (Django-free, fully tested)
│   │   ├── discovery.py         finds manage.py / Django apps on disk (incl. src/, apps/)
│   │   ├── indexer.py           AST-based cross-app import graph
│   │   ├── runtime_report.py    reads/summarizes JSON from djangolens_runtime (Django-free)
│   │   ├── health_score.py      transparent 0-100 score formula, computed on every scan
│   │   ├── cli.py                CLI entry point (`python -m django_doctor.cli`), incl. format_sarif()
│   │   └── analyzers/
│   │       ├── architecture.py   DJANGO001 (circular deps), DJANGOARCH (allow/deny rules)
│   │       ├── n1_detector.py    DJANGO003 (possible N+1 queries)
│   │       ├── migrations.py     DJANGO004 (risky migration operations)
│   │       ├── signals.py        DJANGO005 (recursive-save / sync external calls) +
│   │       │                     find_signal_chains() (every receiver, for visualization)
│   │       ├── settings.py       DJANGO006 (DEBUG/SECRET_KEY/ALLOWED_HOSTS drift, own + inherited) +
│   │       │                     summarize_settings_files() (Settings Visualizer data source)
│   │       ├── models_health.py  DJANGO009/010/011 (on_delete, null/unique, indexes)
│       ├── unused.py         DJANGO012/013 (unused models, missing admin)
│       ├── base.py           Analyzer protocol + registry
│       ├── admin.py          DJANGO007 (ModelAdmin missing list_display/list_filter/search_fields/date_hierarchy/readonly_fields/autocomplete_fields)
│   │       ├── api_diff.py       DJANGO008 (DRF serializer field drift, via git show + ast;
│   │       │                     resolves ModelSerializer's Meta.fields against the model too)
│   │       ├── urls.py           URL Explorer (resolved endpoint list, not a diagnostic)
│   │       └── test_impact.py    Test Impact Analyzer (reverse import-graph BFS, not a diagnostic)
│   │
│   ├── djangolens_runtime/       separate package: install into YOUR Django project
│   │   ├── frames.py             stack-frame filtering (Django-free, tested)
│   │   ├── query_analysis.py     duplicate-query grouping (Django-free, tested)
│   │   ├── instrumentation.py    QueryCollector via connection.execute_wrapper (needs Django, UNTESTED)
│   │   ├── middleware.py         DjangoLensQueryMiddleware (needs Django, UNTESTED)
│   │   └── README.md             install/enable instructions for this package
│   │
│   └── tests/
│       ├── test_architecture.py
│       ├── test_n1_detector.py
│       ├── test_migrations.py
│       ├── test_signals.py
│       ├── test_settings.py
│       ├── test_urls.py
│       ├── test_admin.py
│       ├── test_sarif.py
│       ├── test_test_impact.py
│       ├── test_api_diff.py          builds & commits to real git repos (not mocked)
│       ├── test_frames.py            tests djangolens_runtime.frames (no Django needed)
│       ├── test_query_analysis.py    tests djangolens_runtime.query_analysis (no Django needed)
│       ├── test_runtime_report.py    tests django_doctor.runtime_report against fabricated JSON
│       ├── test_health_score.py      tests the deduction formula and its per-rule cap
│       └── fixtures/sample_project/   a small 3-app Django-shaped fixture,
│                                        including 8 migration files, a
│                                        signals.py, a 4-file settings/
│                                        package, 3 urls.py files, 2
│                                        admin.py files, and 3 test files
│                                        forming a direct + transitive +
│                                        cross-app import chain
│
├── djangolens.config.json      example architecture rules
└── package.json                 extension manifest
```

## Why the analysis engine is Python, not TypeScript

The thing being analyzed is Python/Django source. Using Python's built-in
`ast` module means:

- No parser dependency to vendor or keep in sync with Python grammar changes.
- The same engine is trivially usable as a standalone CLI (`python -m
  django_doctor.cli scan`) for CI, with **no VS Code dependency at all**.
- The VS Code extension is a thin TypeScript shell: it spawns the Python
  process, parses its JSON, and turns the result into `vscode.Diagnostic`
  objects. It never re-implements analysis logic.

This also means editor and CI results are guaranteed identical — they run
the literal same code.

## Data flow

```
VS Code command "DjangoLens: Scan Project"
        │
        ▼
src/extension.ts → src/pythonBridge.ts
        │  spawns: python -m django_doctor.cli scan <root> --format json --config <path>
        ▼
python/django_doctor/cli.py
        │
        ├── discovery.py     → find manage.py, discover app directories
        ├── indexer.py       → ast.walk() every .py file, record cross-app imports
        ├── analyzers/architecture.py
        │       ├── detect_circular_dependencies()   (graph cycle detection)
        │       └── check_architecture_rules()        (config-driven allow/deny)
        ├── analyzers/n1_detector.py
        │       └── scan_file_for_n_plus_one()         (loop + relationship-access heuristic)
        ├── analyzers/migrations.py
        │       └── scan_migrations()                   (per-operation risk rules, dependency-ordered)
        ├── analyzers/signals.py
        │       └── scan_signals()                       (@receiver / .connect() recursion + sync-call rules)
        ├── analyzers/settings.py
        │       └── scan_settings()                       (DEBUG/SECRET_KEY/ALLOWED_HOSTS drift rules)
        └── analyzers/admin.py
                └── scan_admin()                            (ModelAdmin missing list_display)
        │
        ▼
JSON: { project_root, apps, diagnostic_count, diagnostics: [...] }
        │
        ▼
src/extension.ts turns each diagnostic into a vscode.Diagnostic (Problems
panel), stores the raw JSON per file for other providers to read from, and
refreshes the CodeLens/Issues-tree providers.
        │
        ├── src/hoverProvider.ts        → structured hover on the flagged token
        ├── src/codeLensProvider.ts     → inline lens above the flagged line
        ├── src/codeActionProvider.ts   → N+1 quick fix (select_related/prefetch_related)
        └── src/issuesTreeProvider.ts   → "Issues" sidebar view, grouped by severity
```

`analyzers/urls.py` and `analyzers/test_impact.py` are not part of the
diagnostics pipeline above — they power the separate `django-doctor urls`
/ `django-doctor test-impact` commands, each returning its own shape
(`(endpoints, notes)`, and a confidence/hop-annotated test-module list,
respectively) instead of a diagnostics list, since neither is a risk to
flag. `django_doctor/runtime_report.py` is a third such case: it reads
JSON written by the *separate* `djangolens_runtime` package (see below)
rather than analyzing source at all.

`analyzers/api_diff.py` is different again: it DOES return the same
diagnostics shape as `scan`'s analyzers (and supports `--format sarif`
and `--strict` identically), but unlike migrations/signals/settings/admin
— which are each both included in the aggregate `scan` AND available as
their own standalone subcommand — `api-diff` is standalone-only, never
part of `run_scan()`/`scan`. It needs a `--base` git ref argument that
the generic `scan` command has no concept of, and running a git-diff
comparison on every plain `scan` invocation (most of which aren't about
API contracts at all) would be surprising.

## Trust boundary: static analysis vs. the runtime companion package

Every analyzer under `python/django_doctor/analyzers/` (everything that
powers `scan`/`migrations`/`signals`/`settings`/`admin`/`urls`/
`test-impact`/`api-diff`) is 100% static: `ast.parse()` only (`api_diff.py`
also shells out to `git show` to fetch a second revision's file content,
but that's read-only history retrieval, not project execution). Nothing
imports or runs the target Django project, so DjangoLens is safe to run
against a project whose environment (settings, database,
`DJANGO_SETTINGS_MODULE`) isn't configured, or whose `settings.py` has
side effects.

The one exception is `python/djangolens_runtime/` — a genuinely separate
package (different install target: it goes *into the user's own Django
project*, not into DjangoLens' environment) implementing the "ORM Query
Detective" feature from the original spec. This is intentionally kept out
of `django_doctor/` and out of the static `scan` pipeline, because it's a
different trust boundary: it requires the user to explicitly enable
middleware that runs inside their real application. `django_doctor/`
only ever *reads the JSON output* of that middleware
(`runtime_report.py`) — it never imports or invokes `djangolens_runtime`
directly, and never executes anything on the user's behalf.

## Extensibility: adding a new analyzer

1. Add `python/django_doctor/analyzers/your_analyzer.py` exposing a
   function that returns a list of diagnostic dicts with the same shape as
   the existing analyzers (`rule_id`, `analyzer`, `severity`, `confidence`,
   `message`, `explanation`, `suggested_fix`, and location fields when
   applicable).
2. Assign it an unused `DJANGONNN` rule ID and document it in the README's
   rule reference table.
3. Call it from `run_scan()` in `cli.py` and extend `diagnostics`.
4. Add a fixture (or extend `tests/fixtures/sample_project`) covering both
   a true positive and a plausible false positive, and a test module next
   to `test_architecture.py` / `test_n1_detector.py`.

No changes to `discovery.py`, `indexer.py`, the CLI's argument parsing, or
`extension.ts` are needed for a new *static* analyzer — `run_scan()` is the
only integration point. Diagnostics with a `file`/`line`/`col` show up in
the Problems panel, hover, CodeLens, and Issues tree automatically —
there's no longer a class of diagnostic that's CLI/JSON-only (circular
dependencies now attach a representative import location too; see
"Gap fixes" in the README).

## Confidence levels

Every diagnostic declares a `confidence`:

- **`high`** — a fact derivable directly from the AST/import graph with no
  guessing (circular dependencies, configured architecture violations).
- **`static`** — derived from more involved static analysis that isn't a
  simple graph fact but also isn't guessing at runtime behavior (migration
  risk rules, settings drift rules) — the migration analyzer downgrades
  specific findings to `heuristic` when it genuinely can't determine
  history (see `analyzers/migrations.py`).
- **`heuristic`** — pattern-matching that is usually right but can't see
  runtime behavior (the N+1 detector: it can't know whether a variable is
  really a queryset, or whether a relation is already cached; the Admin UX
  Analyzer and Signal Detector are also always `heuristic` for similar
  reasons).

Nothing in this codebase reports `heuristic` findings as `high` or
`static`, and the UI should never let them look the same.
