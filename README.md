# DjangoLens

**Deep Django codebase intelligence for VS Code**

Architecture linting · N+1 detection · Migration risk · Signals · Settings drift · Admin UX · URL explorer · API contract drift · Runtime SQL reports · CI/CLI

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub release](https://img.shields.io/github/v/release/AnandShah10/DjangoLens?include_prereleases&style=flat)](https://github.com/AnandShah10/DjangoLens/releases)
[![GitHub stars](https://img.shields.io/github/stars/AnandShah10/DjangoLens?style=social)](https://github.com/AnandShah10/DjangoLens)
[![GitHub issues](https://img.shields.io/github/issues/AnandShah10/DjangoLens)](https://github.com/AnandShah10/DjangoLens/issues)
[![GitHub last commit](https://img.shields.io/github/last-commit/AnandShah10/DjangoLens)](https://github.com/AnandShah10/DjangoLens/commits)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![VS Code](https://img.shields.io/badge/VS%20Code-1.85%2B-007ACC.svg?logo=visual-studio-code&logoColor=white)](https://code.visualstudio.com/)
[![Django](https://img.shields.io/badge/Django-3.2%2B-092E20.svg?logo=django&logoColor=white)](https://www.djangoproject.com/)

> Static analysis that understands **Django apps → models → ORM → views → URLs → signals → settings → tests** — with actionable diagnostics, Code Actions, sidebar explorers, and a CI-friendly CLI. Local-only by default: your source, SQL, and secrets never leave your machine.

**Made with ❤️ by Anand Shah for Developer Community**

---

## Why DjangoLens?

Generic Python linters do not understand Django. DjangoLens does.

| Problem | What DjangoLens does |
|--------|----------------------|
| Circular app dependencies | Flags cycles and enforces allow/deny architecture rules |
| Hidden N+1 queries | Heuristic static detection + optional runtime SQL capture |
| Risky migrations | Destructive ops, irreversible `RunPython`, nullability traps |
| Signal footguns | Recursive `.save()`, sync network/email inside receivers |
| Settings drift | `DEBUG=True` / hardcoded `SECRET_KEY` reaching production-like envs |
| Admin UX gaps | Suggests `list_display`, filters, search, autocomplete from real fields |
| API contract breaks | Git-based DRF serializer field drift |
| “What tests should I run?” | Import-graph test impact with symbol-level hints |

---

## Configuration & suppression

Example `djangolens.config.json`:

```json
{
  "architecture": { "payments": { "cannotImport": ["orders"] } },
  "analyzers": { "unused": true, "deprecated_apis": true },
  "suppress": ["DJANGO015"],
  "discovery": { "extra_app_roots": ["packages"] }
}
```

Inline: `# django-doctor: ignore DJANGO003`

Full rule list: [docs/rules.md](docs/rules.md)

## Quick start

### VS Code extension

1. Clone this repository (or install from a VSIX you build locally).
2. Open the folder in VS Code, press **F5** to launch the Extension Development Host, **or** package with `vsce package` after `npm install && npm run compile`.
3. Open a Django project (workspace containing `manage.py`).
4. Run **DjangoLens: Scan Project** from the Command Palette.

```bash
# From the extension root
npm install
npm run compile
# F5 in VS Code, or:
# npx vsce package
```

### CLI (no VS Code required)

```bash
cd python
python -m django_doctor.cli scan /path/to/django/project
python -m django_doctor.cli scan /path/to/django/project --format sarif --strict
python -m django_doctor.cli migrations /path/to/django/project
python -m django_doctor.cli models /path/to/django/project
python -m django_doctor.cli unused-urls /path/to/django/project
```

Python **3.10+**. No Django install required for static analysis.

---

## Features at a glance

- **Diagnostics** in the Problems panel with rule IDs, confidence, explanation, and suggested fix
- **Code Actions** for N+1 (`select_related` / `prefetch_related`), `on_delete`, and null/blank fixes
- **Hover** and **CodeLens** on flagged lines
- **Activity Bar** views: Issues (with health score) and URL Explorer
- **Webviews**: Settings overview, Signal chains, API contract drift, Runtime report
- **CLI** with `text` / `json` / `sarif` and `--strict` for CI
- **GitHub Actions** example under `examples/github-actions/`

---

## What it does today

- **Architecture Linter** (`DJANGO001`, `DJANGOARCH`) — detects circular
  dependencies between Django apps, and lets you declare allow-list
  (`canImport`) or deny-list (`cannotImport`) rules between apps via
  `djangolens.config.json`, enforced on every cross-app import.
- **Possible N+1 Query Detector** (`DJANGO003`) — flags relationship
  traversal (`order.customer.name`) inside a loop over what looks like a
  queryset, unless `select_related()`/`prefetch_related()` already covers
  that relation. Labeled `heuristic` confidence, never `high` — see
  [Known limitations](#known-limitations). Comes with a **Code Action
  (lightbulb quick fix)** in the editor: two choices, "Add
  `.select_related("field")`" and "Add `.prefetch_related("field")`" —
  both offered rather than auto-picked, since static analysis can't tell
  a forward FK from a reverse/M2M relation without executing the Django
  app registry (which this extension never does).
- **Migration Risk Analyzer** (`DJANGO004`) — scans migration files for
  destructive operations (`RemoveField`/`DeleteModel`), irreversible data
  migrations (`RunPython`/`RunSQL` with no reverse), non-nullable fields
  added with no default (will fail against existing rows), and index
  additions that can lock large tables. Always `static` confidence — it has
  no database connection, so it never claims to know actual table size.
- **Signal Detector** (`DJANGO005`) — flags `@receiver`-decorated (and
  `.connect()`-registered) `post_save`/`pre_save` handlers that call
  `instance.save()` on the same instance (recursion risk), and receivers
  that make a synchronous network call or send email directly (blocks the
  triggering save/delete on an external service).
- **Settings Drift Detector** (`DJANGO006`) — scans environment settings
  files (`settings/base.py`, `settings/production.py`, etc.) for
  `DEBUG=True` reaching a production-like environment, a hardcoded
  `SECRET_KEY` (in one or several files), and `ALLOWED_HOSTS` wildcards in
  production-like settings. It also catches these when they reach a
  production-like file **only through inheritance** (`from .base import
  *` where `base.py` sets `DEBUG = True` and the production file never
  reassigns it) — the diagnostic points at the exact star-import line
  that pulls the risk in and names the file that actually set it. A value
  the production file assigns itself (even to something unreadable, or
  inside an `if` block) is never reported as inherited. Secret **values**
  are never read into a diagnostic — only the fact that one was
  hardcoded.
- **Admin UX Analyzer** (`DJANGO007`) — flags `ModelAdmin` registrations
  (both `@admin.register(Model)` and `admin.site.register(Model,
  XAdmin)`) missing six separate things, each with a field-accurate
  suggestion parsed from `models.py`, never a generic placeholder:
  `list_display` (always suggested), `list_filter` (when a
  Boolean/ForeignKey/M2M field exists, or any field with a `choices=`
  kwarg regardless of its own type), `search_fields` (when a
  text-like field exists), `date_hierarchy` (when a Date/DateTime field
  exists — suggests the field found first in source order),
  `readonly_fields` (when a field has `auto_now`/`auto_now_add=True`),
  and `autocomplete_fields` (when a relational field's *target model* has
  its own admin registered **anywhere in the project**, not just this
  file — `scan_admin()` collects every app's registrations first; a
  self-referential `ForeignKey('self', ...)` is never suggested, since
  there's no separate related admin to link to).
  (A single-item tuple needs a trailing comma — `('x',)`, not `('x')`,
  which is just a parenthesized string — caught via manual verification
  and covered by a regression test that actually parses the suggestion
  and checks its AST node type.)
- **Model Field Health** (`DJANGO009` / `DJANGO010` / `DJANGO011`,
  `django-doctor models`) — static checks on model field definitions:
  missing `on_delete`, `SET_NULL` without `null=True`, CASCADE on
  high-value relations (customer/user/owner/…), `unique=True` +
  `null=True`, CharField/TextField `null=True` without `blank=True`, and
  explicit `db_index=False` on ForeignKeys. Code Actions available for
  the unambiguous fixes (add `null=True`, add `blank=True`, insert
  `on_delete=…`).
- **Unused Model / Missing Admin** (`DJANGO012` / `DJANGO013`,
  `django-doctor unused`) — best-effort detection of models that are
  never referenced outside their defining file, and models with no
  `admin.site.register` / `@admin.register` anywhere in the project.
  Confidence is heuristic (dynamic imports and external packages will
  produce false positives).
- **Multi-hop Signal Chains** (`django-doctor signal-chains-multi`) —
  extends the one-hop signal visualization by linking receivers that
  call `.save()` on the same or a constructed model to downstream
  receivers for that model. Still static/heuristic; full runtime
  resolution of arbitrary save targets remains impossible without
  executing the project.
- **Analyzer registry** (`django_doctor.analyzers.base`) — formal
  `Analyzer` protocol + `register()` / `run_registered()` so new
  analyzers can be added without rewriting the scan loop.
- **Deprecated Django APIs** (`DJANGO014`, `django-doctor deprecated`) —
  `url()`, `ugettext*`, `force_text`, `NullBooleanField`, `DEFAULT_FILE_STORAGE`, etc.
- **Unused URL names** (`DJANGO015`, `django-doctor unused-urls`) —
  `name=` on path()/re_path() never reverse()'d in the project.
- **Unregistered signal handlers** (`DJANGO016`, `django-doctor unused-signals`).
- **Symbol-level test impact hints** — affected tests also report which
  symbols from the changed module they import.
- **Performance workflow commands** — `DjangoLens: Analyze Django Performance`
  and `Setup Runtime Instrumentation` (explicit, local-only; never auto-runs Django).
- **URL Explorer** (`django-doctor urls`) — resolves Django's `urlpatterns`
  starting from `ROOT_URLCONF`, following `include(...)` chains across
  files and concatenating route prefixes, into a flat list of endpoints
  with their view, `name=`, and source location. Not a diagnostic
  analyzer — it's project navigation data, not a set of risks.
- **Test Impact Analyzer** (`django-doctor test-impact`) — given a changed
  file, builds a project-wide import graph and walks it in reverse to find
  test modules that likely need re-running: `high` confidence for a test
  that directly imports the changed module, `likely` for anything reached
  through further hops of in-project imports. Also not a diagnostic
  analyzer.
- **ORM Query Detective** (`django-doctor runtime-report`) — the one
  **runtime** (not static) feature: an opt-in Django middleware
  (`python/djangolens_runtime/`, a separate package you install into
  *your* Django project — see its own README) captures every SQL query
  per request, groups duplicates by source location (the runtime
  signature of N+1), and writes a JSON report DjangoLens summarizes. The
  query-grouping and source-mapping logic (`query_analysis.py`,
  `frames.py`) has no Django dependency and is unit-tested directly; the
  middleware itself requires Django to run and **has not been executed
  against a real Django project** — see [Known limitations](#known-limitations).
- **API Contract Drift Detector** (`DJANGO008`, `django-doctor api-diff`)
  — compares DRF serializer fields between two git revisions (default: a
  base ref you give vs. the current working tree), via `git show` + `ast`
  — no Django/DRF import, no HTTP traffic inspected. Covers both
  explicitly-declared `Field()` objects and `ModelSerializer`'s
  `Meta.fields`/`"__all__"` shorthand (resolved against the actual model,
  fetched at the matching git ref for each side of the diff). Flags
  removed fields and type changes as `error` (breaking), a field becoming
  required as `warning`, and additions/fields-becoming-optional as
  `information`. Deliberately never claims which consumers are affected —
  only field declarations are compared, and the original spec explicitly
  cautions against fabricating that. Verified end-to-end against real git
  repositories (not mocked) in the test suite.

All of the above except the ORM Query Detective run as 100% static
analysis (Python's `ast` module only, plus `git show` for the API drift
detector) — **no project code is ever executed**, so it works even
against a Django project whose environment isn't configured.

DjangoLens also has two visualization/UX features layered on top of the
analyzers above:

- **Health score** (`health_score.py`) — a transparent, documented 0-100
  score on every `scan`, computed by deducting per-diagnostic points
  (weighted by severity and confidence) and capping the total deduction
  any single rule ID can contribute, so one noisy heuristic rule can't
  alone tank the score. The exact formula is in the module's docstring —
  see [Rule reference](#rule-reference) for how severity/confidence map
  to points.
- **Settings Visualizer** (`django-doctor settings-overview` / VS Code's
  "DjangoLens: Show Settings Overview" command) — a table of every
  settings file's current DEBUG/SECRET_KEY/ALLOWED_HOSTS state (never
  secret values), distinct from the Settings *Drift Detector* above,
  which only flags risks rather than showing the full picture. Values are
  **effective**: a file that star-imports another (`from .base import *`)
  and doesn't assign a setting itself shows the inherited value, tagged
  with where it came from — following chains of imports, with a later
  import overriding an earlier one as Python does.

## Install (development)

```bash
# VS Code extension
npm install
npm run compile        # or: npm run watch
# then press F5 in VS Code to launch an Extension Development Host
```

The extension shells out to a Python 3 interpreter to run the analyzer — no
`pip install` is required, since `python/django_doctor` only uses the
standard library. Set `djangolens.pythonPath` in settings if `python3` isn't
on your PATH.

## Using it

DjangoLens now has its own icon in the Activity Bar. Open a folder
containing a Django project (one with `manage.py`) and either:

- Run **DjangoLens: Scan Project** from the Command Palette — issues
  appear in the Problems panel (grouped by file, with severity, an
  explanation, and a suggested fix where one exists), inline **CodeLens**
  above each flagged line, and a richer structured **hover** (rule ID,
  confidence, explanation, suggested fix) when you hover the flagged
  token. For possible-N+1 diagnostics specifically, the 💡 lightbulb
  offers a real **Code Action**: "Add `.select_related(...)`" or "Add
  `.prefetch_related(...)`" — both insert safely, and you pick the one
  that matches your model, since DjangoLens can't determine the
  relation's direction statically.
- Open the **DjangoLens** icon in the Activity Bar for two views:
  **Issues** (a health-score header, then every diagnostic from the last
  scan grouped by severity, click to jump to source) and **URLs** (every
  resolved endpoint, grouped by first path segment, click to jump to its
  `path()`/`re_path()` call — refreshes automatically whenever a
  `urls.py` changes, or via the refresh icon in either view's title bar).
- Right-click a Python file (in the editor or Explorer) for **DjangoLens:
  Analyze Test Impact** — shows the affected test modules (with
  confidence and hop count) in a Quick Pick; selecting one opens it. Also
  available from the Command Palette, using the active editor's file.
- Run **DjangoLens: Show Settings Overview** from the Command Palette for
  a table of every settings file's current DEBUG/SECRET_KEY/ALLOWED_HOSTS
  state (never secret values) in a webview panel.
- Run **DjangoLens: Show Runtime Report** from the Command Palette,
  point it at the directory `djangolens_runtime`'s middleware writes JSON
  reports to (see below), and get a sorted-slowest-first table with each
  endpoint's query count and top duplicate-query issue — the "Why is this
  slow?" view from the original spec, minus the one-click trigger (this
  reads reports you've already captured; it never runs Django itself).
- Run **DjangoLens: Compare API Contract** from the Command Palette,
  enter a base git ref (and optionally a head ref — defaults to the
  current working tree) when prompted, and get a webview listing every
  DRF serializer field drift between them, same severity levels as the
  CLI's `api-diff`.
- Run **DjangoLens: Show Signal Chains** from the Command Palette for a
  visual sender → signal → receiver → effects diagram of every Django
  signal receiver (both `@receiver`-decorated and `.connect()`-registered),
  with risky ones (recursive save, network call, email) highlighted.

## Using the CLI (no VS Code required)

The same analysis engine is a standalone CLI, so it works identically in
CI:

```bash
cd python
python -m django_doctor.cli scan /path/to/django/project --format text
python -m django_doctor.cli scan /path/to/django/project --format json
python -m django_doctor.cli scan /path/to/django/project --config djangolens.config.json --strict

# Migration Risk Analyzer only — a focused pre-deploy check
python -m django_doctor.cli migrations /path/to/django/project --strict

# Signal Detector only
python -m django_doctor.cli signals /path/to/django/project

# Settings Drift Detector only
python -m django_doctor.cli settings /path/to/django/project

# Admin UX Analyzer only
python -m django_doctor.cli admin /path/to/django/project

# URL Explorer — resolved endpoint list, not a diagnostic command (no --strict)
python -m django_doctor.cli urls /path/to/django/project

# Test Impact Analyzer — which tests likely need re-running for a changed file
python -m django_doctor.cli test-impact /path/to/django/project orders/models.py

# ORM Query Detective — summarize reports from the djangolens_runtime
# middleware (see python/djangolens_runtime/README.md to enable it in
# YOUR Django project first — this reads its output, it doesn't run Django)
python -m django_doctor.cli runtime-report /path/to/.djangolens/runtime

# API Contract Drift Detector — compare DRF serializers between two git refs
python -m django_doctor.cli api-diff /path/to/django/project --base main
python -m django_doctor.cli api-diff /path/to/django/project --base main --head feature-branch

# Settings Visualizer — current DEBUG/SECRET_KEY/ALLOWED_HOSTS state per file
python -m django_doctor.cli settings-overview /path/to/django/project

# Signal chain visualization data — every receiver (risky or not), not just flagged ones
python -m django_doctor.cli signal-chains /path/to/django/project
```

`scan`'s output also includes a `health` score (0-100) — see the "Health
score" bullet above for the formula, and `--format json` for the full
per-rule deduction breakdown.

`--strict` exits with status 1 if any `error`-severity diagnostic is found
(architecture violations, circular dependencies, destructive migrations,
non-nullable `AddField` with no default, removed API fields, and API field
type changes are all `error`; possible N+1 queries, index additions,
irreversible `RunPython`/`RunSQL`, and new required API fields are
`warning`) — useful as a CI gate.

Every diagnostics command (`scan`, `migrations`, `signals`, `settings`,
`admin`, `api-diff` — not `urls`, `test-impact`, `runtime-report`,
`settings-overview`, or `signal-chains`, none of which is diagnostics) also accepts
`--format sarif`, producing a
[SARIF 2.1.0](https://sarif-tutorials.readthedocs.io/) result set. This is
the format GitHub's code scanning (Security tab / PR annotations) and most
other CI dashboards consume natively:

```bash
python -m django_doctor.cli scan /path/to/django/project --format sarif > results.sarif
```

See `examples/github-actions/djangolens.yml` for a copy-paste GitHub
Actions workflow that uploads this SARIF output and gates PRs on
migration risk.

## Configuration

`djangolens.config.json` at your project root (or passed via `--config`):

```json
{
  "architecture": {
    "orders": { "canImport": ["users", "payments"] },
    "payments": { "cannotImport": ["orders"] }
  }
}
```

- `canImport` is an **allow-list**: any cross-app import not in the list is
  a violation.
- `cannotImport` is a **deny-list**: only the listed apps are violations.
- An app with no rule configured is never flagged — architecture rules are
  opt-in per app.

## Rule reference

| Rule ID      | Analyzer     | Severity | Confidence | Meaning |
|--------------|--------------|----------|------------|---------|
| `DJANGO001`  | architecture | error    | high       | Circular dependency between Django apps |
| `DJANGOARCH` | architecture | error    | high       | Import violates a configured `canImport`/`cannotImport` rule |
| `DJANGO003`  | orm          | warning  | heuristic  | Possible N+1 query: relationship traversal on a loop variable from an unoptimized queryset |
| `DJANGO004`  | migrations   | error/warning/information | static | Risky migration operation — destructive (error), irreversible/index/nullability (warning), or informational review note |
| `DJANGO005`  | signals      | warning  | heuristic  | Recursive-save risk in a post_save/pre_save receiver, or a synchronous network/email call inside a receiver |
| `DJANGO006`  | settings     | error/warning | static | DEBUG=True in production-like settings (error), hardcoded SECRET_KEY or wildcarded ALLOWED_HOSTS (warning/error depending on environment) |
| `DJANGO007`  | admin        | information | heuristic | `ModelAdmin` missing `list_display`, `list_filter`, `search_fields`, `date_hierarchy`, `readonly_fields`, or `autocomplete_fields` — each only when a matching field/registration exists; suggested fixes name the model's real fields |
| `DJANGO008`  | api          | error/warning/information | static | DRF serializer field removed or type-changed (error), field became required (warning), field added or became optional (information) — compared across two git revisions |
| `DJANGO009`  | models_health | error/information | high/heuristic | Missing `on_delete`, `SET_NULL` without `null=True` (error), or CASCADE on a high-value relation name (information) |
| `DJANGO010`  | models_health | warning | high/heuristic | `unique=True` + `null=True`, or CharField/TextField `null=True` without `blank=True` |
| `DJANGO011`  | models_health | information | heuristic | ForeignKey with explicit `db_index=False` |
| `DJANGO012`  | unused       | information | heuristic | Model class never referenced outside its defining file |
| `DJANGO013`  | unused       | information | heuristic | Model has no admin registration |
| `DJANGO014`  | deprecated_apis | error/warning | high/heuristic | Deprecated Django API (url(), ugettext, NullBooleanField, DEFAULT_FILE_STORAGE, …) |
| `DJANGO015`  | unused_urls  | information | heuristic | URL `name=` never referenced via reverse()/reverse_lazy() |
| `DJANGO016`  | unused_signals | information/warning | heuristic | Signal-like handler not registered, or .connect() to unknown name |

## Running the tests

No network/package installs required — everything uses the Python standard
library:

```bash
cd python
python -m unittest discover -s tests -v
```

215 tests cover both true positives and deliberate false-positive checks for
each analyzer (see `tests/fixtures/sample_project`, a 3-app fixture with a
real circular dependency, an architecture violation, a flagged and a
`select_related()`-optimized N+1 case, 8 migration files covering
destructive ops/irreversible RunPython/risky AddField/AddIndex and both a
true-positive and a fixed false-positive AlterField nullability case, a
signals.py with a safe receiver plus recursive-save, network-call, and
`.connect()`-registered receivers, a 4-file settings/ package covering
DEBUG, SECRET_KEY, and ALLOWED_HOSTS across dev/staging/production, a
config/urls.py + two app-level urls.py resolving a full include() chain,
two admin.py files — one flagged (decorator registration, no
list_display) and one clean (imperative registration, already configured)
— and 3 test files (`orders/tests/test_models.py`,
`orders/tests/test_views.py`, `payments/tests/test_payment.py`) forming a
real direct + 2-hop-transitive + cross-app-transitive import chain for the
Test Impact Analyzer — plus tests proving `src/`- and `apps/`-nested app
layouts are now discovered, a SARIF-output test suite (valid shape,
relative forward-slash paths, every diagnostic accounted for, and an
actual subprocess round-trip through the real CLI), a Django-free test
suite for the runtime companion package's query-grouping and
stack-frame-filtering logic (`query_analysis.py`/`frames.py` — the parts
that don't need Django installed to test), a `runtime_report.py`
suite driven by fabricated JSON reports simulating exactly what the
middleware would write, an API Contract Drift Detector suite that
builds and commits to **real git repositories** (not mocked) to verify
the full `git show` → `ast` → field-diff pipeline end to end, including
the exact scenario that caught a real bug during manual verification
(serializer-only apps with no `models.py`), a health-score suite covering
the deduction formula and its per-rule cap, a
`summarize_settings_files()` suite (the Settings Visualizer's data
source) reusing the same 4-file settings/ fixture, and an Admin UX
Analyzer suite that includes a regression test for a second real bug
caught via manual verification (`list_filter = ('x')` isn't a tuple —
needs a trailing comma). The N+1 detector's test suite
also includes an end-to-end correctness check: it splices the suggested
`.select_related(...)` fix into the real source at the computed insertion
point and confirms the result is both valid Python and no longer flagged
on re-scan.

## Implemented vs. deferred

The original product spec (see the prompt this was built from) covers 11
analyzers plus runtime instrumentation, API contract drift, CI/SARIF
output, and a full VS Code UX (tree views, CodeLens, hover, Code Actions).
Building all of that as a first pass would mean either an unrealistic
amount of code or a lot of `// TODO` stubs pretending to be finished —
neither is useful to you. Here's what's actually real:

**Implemented**
- Project/app discovery (`manage.py`, app directories via `models.py`/
  `apps.py`, including apps nested up to a few levels deep under `src/` or `apps/`)
- Cross-app import indexing via `ast`
- Architecture Linter: circular dependency detection + configurable allow/deny rules
- Possible N+1 Query Detector (static heuristic) with a real editor **Code
  Action** (quick fix) offering `.select_related(...)`/`.prefetch_related
  (...)` insertion — not auto-applied, since picking the right one needs
  the developer's knowledge of the relation's actual direction
- Migration Risk Analyzer (destructive ops, irreversible RunPython/RunSQL,
  risky AddField/AlterField nullability, AddIndex locking risk, now
  dependency-graph ordered rather than filename-sorted) + a dedicated
  `django-doctor migrations` CLI command
- Signal Detector (recursive-save risk, synchronous network/email calls in
  both `@receiver`-decorated and `.connect()`-registered handlers) + a
  dedicated `django-doctor signals` CLI command
- Settings Drift Detector (DEBUG in production-like environments, hardcoded
  SECRET_KEY, wildcarded ALLOWED_HOSTS — whether set in the file itself or
  inherited via `from .base import *` — never reads secret values) + a
  dedicated `django-doctor settings` CLI command
- Admin UX Analyzer: `ModelAdmin` missing `list_display`, `list_filter`,
  `search_fields`, `date_hierarchy`, `readonly_fields`, or
  `autocomplete_fields` (the last needs whole-project context — see
  above), each suggested with real fields from `models.py` + a dedicated
  `django-doctor admin` CLI command
- URL Explorer: resolves `urlpatterns` from `ROOT_URLCONF` through nested
  `include(...)` calls into a flat, prefix-concatenated endpoint list +
  `django-doctor urls` CLI command
- Test Impact Analyzer: reverse-walks a project-wide static import graph
  from a changed file to find `high`/`likely`-confidence affected test
  modules + `django-doctor test-impact` CLI command
- ORM Query Detective (runtime): an opt-in Django middleware companion
  package (`python/djangolens_runtime/`) capturing per-request SQL,
  grouping duplicates by source location, read via `django-doctor
  runtime-report` — the query-grouping/source-mapping logic is Django-free
  and unit-tested; the middleware itself is **unverified** (no Django in
  this environment) — see [Known limitations](#known-limitations)
- API Contract Drift Detector (`DJANGO008`): compares DRF serializer
  fields between two git revisions (`git show` + `ast`, no Django/DRF
  import), flagging removals/type-changes as breaking and new-required
  fields as a narrower risk + `django-doctor api-diff` CLI command —
  verified end-to-end against real git repositories in the test suite,
  not mocked
- **Full VS Code UI**: a dedicated Activity Bar icon/container with two
  sidebar views (**Issues** — a health-score header, then all diagnostics
  grouped by severity; **URLs** — the resolved endpoint tree,
  live-refreshing on `urls.py` changes), diagnostics in the Problems
  panel, a **HoverProvider** with structured rule/confidence/explanation/
  fix content, a **CodeLens** above every flagged line, a **Code Action**
  (quick fix) for N+1 diagnostics, an **Analyze Test Impact** command
  (editor/Explorer context menu + Command Palette, Quick Pick results),
  and a **Settings Overview** webview
- Health score: a transparent, documented 0-100 formula (severity- and
  confidence-weighted deductions, capped per rule ID so no single noisy
  rule dominates) on every `scan` — see `health_score.py`'s docstring for
  the exact formula
- Settings Visualizer: a table of every settings file's current
  DEBUG/SECRET_KEY/ALLOWED_HOSTS state (never secret values), following
  `from .base import *` inheritance — distinct from the Settings *Drift
  Detector*, which only flags risks — via `django-doctor
  settings-overview` and a VS Code webview
- **CI**: `--format sarif` on every diagnostics command (GitHub code
  scanning / most CI security dashboards consume this natively) + a
  copy-paste GitHub Actions template (`examples/github-actions/djangolens.yml`)
- Runtime Report webview: "DjangoLens: Show Runtime Report" reads
  `djangolens_runtime`'s JSON output into a sorted-slowest-first table
  with each endpoint's top duplicate-query issue — the display half of
  the original spec's "Why is this slow?" feature (it reads captured
  reports; it never triggers Django execution itself)
- API Contract Drift webview: "DjangoLens: Compare API Contract" prompts
  for a base (and optional head) git ref and shows the same field-drift
  diagnostics as `api-diff` in a webview
- Signal chain visualization: `django-doctor signal-chains` and a
  "DjangoLens: Show Signal Chains" webview render every Django signal
  receiver (`@receiver`-decorated and `.connect()`-registered alike, risky
  or not) as a sender → signal → receiver → effects diagram, with
  recursive-save/network-call/email effects highlighted
- Standalone CLI with text/JSON/SARIF output and a `--strict` CI-exit-code mode
- 236+ passing unit tests with both true- and false-positive coverage
- Model field health, unused-model / missing-admin detection, multi-hop
  signal chains, and a formal Analyzer registry (see "What it does today")

**Deferred** (from the original spec, intentionally not started or only partial)
- "Why is this slow?" **one-click trigger**: the spec's context-menu
  "Analyze Django Performance" that runs a request and shows results
  immediately. The Runtime Report *webview* exists and reads
  already-captured reports, but nothing in the extension triggers a
  Django request itself — that would mean executing the user's project
  from the extension, a trust boundary this build has stayed on the safe
  side of throughout (see `djangolens_runtime/README.md`).
- Perfect multi-hop signal resolution: `signal-chains-multi` links
  same-sender recursive saves and constructed-model saves when the class
  name is statically visible, but cannot resolve arbitrary
  `some_var.save()` targets without a full type system / runtime.
- Code Actions for architecture violations and migration risks remain
  intentionally absent (matching the spec's caution: "Do NOT
  automatically rewrite architecture unless the fix is unambiguous").
  N+1, on_delete, and null/blank Code Actions are implemented.
- App discovery under container directories other than `src/`/`apps/`,
  apps nested more than a few levels deep, and dynamically-registered apps
- Runtime-verified table sizes for migration risk (correctly never fabricated)
- Fully automatic multi-hop signal resolution for arbitrary `.save()` targets
- Executing the user's Django process from the extension (explicitly out of scope)

## Known limitations

- **The health score's weights are one reasonable formula, not a
  validated standard.** Deduction points per (severity, confidence) pair
  and the per-rule cap are documented constants in `health_score.py`, not
  derived from any industry benchmark — teams that disagree should treat
  `deduction_by_rule` (in `--format json`) as the real audit trail and
  adjust from there rather than trusting the single number blindly.
- **The Settings Visualizer** ~~doesn't resolve `from .base import *`
  inheritance~~ — **fixed**: each file now shows its *effective* values
  and where they came from (`extends`, `inherited_from` in the JSON; a
  "from base" tag in the webview), following chains and honoring Python's
  later-import-wins order. The per-file own-value keys are unchanged, so
  existing consumers keep working. Deliberately conservative: a setting a
  file assigns to something non-literal (`DEBUG = compute_debug()`), or
  assigns only inside an `if`/`try` block, stays "?" — it does **not**
  fall back to the parent's value, since the file overrides it with
  something unknowable and a confident wrong answer is worse than an
  honest "unknown". Still real limits: only `from X import *` is
  followed (not `from . import base` or `import_module`), the parent must
  be another discovered settings file in the project, and it's a flat
  table with "extends" tags rather than the spec's drawn BASE→DEV/STAGING/
  PROD tree. The Settings Drift *Detector* uses the same inheritance
  resolution (they share `_build_settings_records`), so what the overview
  shows and what gets flagged can't disagree — see the next entry.

- **The API Contract Drift Detector** ~~only compares explicit field
  declarations, and doesn't resolve `ModelSerializer`'s `Meta.fields`
  back to the underlying model~~ — **fixed**: a `ModelSerializer` that
  only lists field *names* in `Meta.fields` (or uses `"__all__"`), with
  no explicit `Field()` declarations at all, now resolves those names
  against the actual model's field types — fetched at the *same git ref*
  as the serializer being compared, so a model field that changed type
  between revisions is correctly detected even when the serializer source
  itself didn't change. An explicit `Field()` declaration in the
  serializer still always overrides the Meta-resolved one, matching DRF's
  own behavior. What's still a real limitation: it can't tell whether a
  serializer is used for input (request validation) vs. output (response
  shape) vs. both — "new required field" is flagged the same way
  regardless, though the real breaking-change risk is specific to input
  use. The model lookup also assumes `models.py` sits in the same
  directory as the serializer (same assumption the Admin UX Analyzer
  makes) and only understands simple `field = models.XField(...)`
  assignments — the same shape limits apply as everywhere else model
  fields are parsed in this project.
- **`djangolens_runtime` (the ORM Query Detective middleware) has not
  been executed.** This build environment has no Django installed, so
  `instrumentation.py` and `middleware.py` were written directly against
  Django's documented APIs but never actually run — treat them as
  unverified until you try them against a real project. The parts that
  don't need Django (`query_analysis.py`'s duplicate-query grouping,
  `frames.py`'s stack-frame filtering) have no such caveat — they're
  pure Python and are genuinely unit-tested. Also: duplicate-query
  grouping requires both matching SQL shape *and* matching source
  location, so an N+1 that fans out through more than one call site for
  the same logical loop won't be grouped as one issue — see
  `djangolens_runtime/README.md`.

- **The Test Impact Analyzer only sees static, in-project imports.** It
  doesn't understand pytest fixtures, Django's test discovery/tags,
  dependency injection, or dynamic imports — a test that exercises changed
  code only through one of those (with no static import chain to it) won't
  be found. It also doesn't do symbol-level tracking (e.g. "only the
  `calculate_total` method changed") — the granularity is whole files. And
  for apps discovered under `src/`/`apps/` (see below), the computed module
  path includes that container segment (`src.orders.models`), which may
  not match the app's real import path if `src/` is added to `sys.path` —
  this can cause a missed edge at that boundary.
- **The GitHub Actions template checks out DjangoLens as a second repo**,
  since it isn't published to PyPI yet — see the comment at the top of
  `examples/github-actions/djangolens.yml`. This also means the template
  itself has never actually run in a real GitHub Actions environment
  (this sandbox has no network access to test it against GitHub) — it was
  validated for YAML syntax only, not executed end-to-end.
- **App discovery** recognizes flat-layout apps, plus apps nested under a
  `src/` or `apps/` directory at the project root **up to three extra
  levels deep** (so `src/myproject/orders/` works, not just `src/orders/`)
  — ~~one level only~~, fixed. It does not descend into a directory it
  has already identified as an app (so a stray `models.py`-bearing
  subfolder inside an app doesn't become a phantom second app). Still
  not discovered: apps under container directories with any other name,
  or nested deeper than the limit.
- **The N+1 detector is a syntactic heuristic**, not a query planner. It
  cannot know whether a variable actually holds a Django queryset (it
  matches on `.objects`/`.all`/`.filter`/`.exclude` appearing in the
  assigned expression) or whether a relation is already cached from an
  earlier optimized query elsewhere. It intentionally reports nothing
  rather than guessing when the iterable isn't recognizable — false
  negatives are preferred over false positives here.
- **The Migration Risk Analyzer can't see history.** ~~`AlterField` with
  `null=False` is flagged unconditionally~~ — **fixed**: `scan_migrations()`
  now tracks field nullability across an app's migration sequence
  (`CreateModel`→`AddField`→`AlterField`), so `AlterField(null=False)` is
  only flagged as a real regression when an earlier migration in the same
  app shows the field was `null=True`. If the field was already
  non-nullable, nothing changed and it's correctly not flagged. If history
  genuinely can't be determined (e.g. a squashed migration, or
  `analyze_migration_file()` called on a single file in isolation), it's
  still flagged, but as `heuristic` confidence with a message that says so
  explicitly — not `static`. ~~File ordering is by numeric filename
  prefix, not Django's actual `dependencies = [...]` graph~~ — **also
  fixed**: `_ordered_migration_filenames()` now topologically sorts each
  app's migrations from their real `dependencies` entries, falling back to
  filename order only if the graph doesn't fully resolve (e.g. a
  dependency pointing outside the visible directory, as with a squashed
  migration). Table size is never known (no database connection), so
  "expensive on a large table" is always a hedge, never a measurement —
  see the `RUNTIME/DATABASE VERIFIED` confidence tier from the original
  spec, which is deferred.
- **The N+1 Code Action, hover, and CodeLens correlate by file + line +
  column — fixed** (previously line only, which could ambiguously match
  two flagged relations on the same line; now the exact range-start column
  is matched too, which is unique per token). They still only reflect the
  **last scan's** results, not live re-analysis on every keystroke — run
  **DjangoLens: Scan Project** again after editing to refresh them.
- **The Admin UX Analyzer's suggestions all depend on finding the
  model's `models.py`** in the same app directory and parsing a
  `class <Model>(...):` with simple `field = models.XField(...)`
  class-body assignments — fields defined via `**kwargs` unpacking won't
  be picked up, so a suggestion that depends on those fields (any of the
  six checks) falls back to not firing rather than guessing. ~~Abstract
  base classes weren't followed~~ — **fixed for the common case**: fields
  from an abstract base (`class Meta: abstract = True`) defined in the
  *same* `models.py` are now included, before the subclass's own fields
  (matching Django's real ordering), including across multiple levels of
  abstract inheritance. Two things remain, one a real gap and one
  deliberate: an abstract base defined in a *different file* (e.g.
  `from core.models import TimestampedModel`) is still not followed; and
  a *concrete* base (multi-table inheritance) is intentionally NOT
  followed, since its fields live on a separate table rather than being
  copied onto the child — following it would misreport the child. ~~The `list_filter`
  fixed type list doesn't recognize a `CharField` with `choices=`~~ —
  **fixed**: any field with a `choices=` kwarg is now a `list_filter`
  candidate regardless of its own type, alongside the original
  `BooleanField`/`ForeignKey`/`ManyToManyField` list.
- **The Signal Detector ~~only recognizes `@receiver(...)`-decorated
  functions~~ — fixed**: it now also resolves `signal.connect(handler,
  sender=Model)` registrations, for both a named function defined elsewhere
  in the file and an inline `lambda`. What's still a real limitation: it
  can't see whether a `.save()` call is actually guarded by conditional
  logic elsewhere in a way that prevents recursion — it flags the call
  unconditionally, so a properly-guarded re-save is a plausible false
  positive. It also won't resolve a handler passed by reference from a
  different file (e.g. imported and then passed to `.connect()`).
- **The Settings Drift Detector only understands module-level, literal
  assignments** (its own, plus inherited ones via `from X import *` — the
  inherited-risk check is a deliberate, tested addition; see
  "Settings Visualizer" above for what is and isn't followed).
  `SECRET_KEY = os.environ["SECRET_KEY"]` is correctly not flagged
  (good), but so is a hardcoded secret computed inside a function or
  hidden behind a helper call — it only recognizes a plain string literal
  assigned directly to the name. One consequence worth knowing: a base
  file that hardcodes `SECRET_KEY` is reported at the base (medium) *and*
  again at each production-like file that inherits it (high) — same root
  cause, deliberately shown at both the source and the point of impact. "Production-like" detection is a
  filename heuristic (`prod`/`production`/`staging`/`stage` in the
  filename) — a project with unconventional environment file names won't
  be recognized, and DEBUG/ALLOWED_HOSTS checks are silently skipped for
  those files rather than guessed at.
- **The URL Explorer can't resolve dynamic `include()` targets.**
  `include(router.urls)` (a DRF router's dynamically-generated patterns) is
  silently skipped — it can't be resolved without executing project code.
  It also doesn't derive HTTP methods from the view (that would require
  understanding DRF ViewSets/generic class-based views), and if no
  `ROOT_URLCONF` can be found it falls back to parsing every `urls.py`
  independently, without concatenating prefixes across files.
- **Circular-dependency diagnostics now have a file location — fixed.**
  `detect_circular_dependencies()` attaches a representative import
  statement's file/line (the cycle's first edge) to each report, so it
  renders in the Problems panel like any other diagnostic instead of only
  appearing in the CLI/JSON output.
- The extension has not been compiled/type-checked against the real
  `@types/vscode` package in this environment (no network access to
  `npm install` here). It was, however, run through `tsc --noEmit` against
  a stub `vscode`/Node-builtin module (an ambient `any`-typed declaration)
  as a syntax/reference smoke test — this catches typos, unbalanced
  braces, unresolved local imports, and undefined variables, but **not**
  real type errors against the actual VS Code API surface (wrong method
  names, wrong argument types, etc.). Run `npm install && npm run compile`
  yourself before trusting it fully.

## Privacy

Everything here runs locally. No source code, file paths, or analysis
results are sent anywhere — the extension only spawns a local Python
process and reads its stdout.


---

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Extension + analyzer layout |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to develop and test |
| [SECURITY.md](SECURITY.md) | Privacy model and reporting |
| [docs/rules.md](docs/rules.md) | Rule IDs, confidence, suppression |
| [python/djangolens_runtime/README.md](python/djangolens_runtime/README.md) | Runtime SQL middleware setup |

---

## Requirements

- **VS Code** 1.85 or later
- **Python** 3.10+ for the analyzer CLI and extension bridge
- **Django** 3.2+ projects supported for static patterns (Django does not need to be importable for static scans)
- Optional: Git, for API contract drift (`api-diff`)

---

## Privacy

- Analysis runs **locally** on your machine.
- Source code, SQL, environment variables, and secrets are **not** sent to external servers.
- Runtime middleware writes JSON reports only to a directory you configure.
- `SECRET_KEY` values are never shown in diagnostics or UI.

See [SECURITY.md](SECURITY.md) for details.

---

## Contributing

Issues and PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
cd python
python -m pytest tests/ --ignore=tests/fixtures -q
```

---

## License

[MIT](LICENSE) © Anand Shah

---

**Made with ❤️ by Anand Shah for Developer Community**
