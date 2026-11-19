# Changelog

## 0.25.0 — Suppressions, analyzer toggles, model graph, template URL scan

- Inline `# django-doctor: ignore RULE` and config `suppress` / `analyzers` toggles
- `discovery.extra_app_roots` for non-src/apps layouts
- Static model relation graph biases N+1 Code Action preferred optimizer
- Unused URL detection scans `.html` templates for `{% url %}`
- Code Action for DJANGO011 (`db_index`)
- [docs/rules.md](docs/rules.md) rule reference


## 0.24.0 — Unused URLs/signals, deprecated APIs, symbol test impact, performance UX

- **DJANGO014** deprecated Django API scanner (`django-doctor deprecated`)
- **DJANGO015** unused URL name detection (`django-doctor unused-urls`)
- **DJANGO016** unregistered signal handlers (`django-doctor unused-signals`)
- Broader **DJANGO011** indexing heuristic for lookup-like CharField/SlugField/EmailField
- **Symbol-level test impact** (`imported_symbols` / `defined_symbols` on results)
- VS Code commands: **Analyze Django Performance**, **Setup Runtime Instrumentation**
  (explicit workflow; still does not execute the user's Django process)
- Wired into full `scan`; 236 tests passing


## 0.23.0 — Model field health, unused detection, multi-hop signals, analyzer registry

Added (closes major gaps against the original Django Doctor product spec):
- **Model Field Health Analyzer** (`DJANGO009` / `DJANGO010` / `DJANGO011`,
  CLI `django-doctor models`): missing `on_delete`, `SET_NULL` without
  `null=True`, CASCADE on high-value relation names, `unique=True`+`null=True`,
  CharField/TextField `null=True` without `blank=True`, `db_index=False` on FKs.
- **Unused Model / Missing Admin** (`DJANGO012` / `DJANGO013`, CLI
  `django-doctor unused`): models never referenced outside their defining
  file; models with no admin registration.
- **Multi-hop signal chains** (`django-doctor signal-chains-multi`): links
  recursive same-sender saves and constructed-model saves to downstream
  receivers where the class name is statically visible.
- **Formal Analyzer protocol + registry** (`analyzers/base.py`):
  `Analyzer`, `AnalysisContext`, `register()`, `run_registered()`.
- **Expanded Code Actions**: lightbulb fixes for SET_NULL→null=True,
  missing on_delete (CASCADE/PROTECT/SET_NULL choices), and CharField
  blank=True — in addition to the existing N+1 select_related/prefetch_related.
- Wired into the full `scan` command; 11 new unit tests (226 total).


## 0.22.0 — Settings Drift Detector flags risks that arrive via inheritance

Added (closes the caveat documented in 0.21.0):
- `scan_settings()` now also raises DJANGO006 diagnostics for a
  production-like file whose *effective* `DEBUG=True`, wildcard
  `ALLOWED_HOSTS`, or hardcoded `SECRET_KEY` arrives purely through
  `from .base import *` — the spec's headline "DEBUG=True reaching
  staging/production" risk, which the own-file-only rule could never see.
  Severities mirror the own-file rules (error / warning / error).
- The diagnostic points at the **star-import line** that brings the risk
  in (not line 1), and the message names the file that actually *set* the
  value, even through a multi-hop chain (`production -> common -> base`
  reports line of `from .common import *` and "from 'base'").
- Conservative by design, all tested: a value the file assigns itself
  (even to something unreadable, or inside an `if`) is never reported as
  inherited; a later star-import overriding an earlier one is honored;
  non-prod-like files, import cycles, and unparseable ancestors are
  handled; the own-file rule and the inherited rule never double-report
  the same assignment; secret values never appear in output.
- Refactor: record-building/star-import resolution extracted into
  `_build_settings_records()`, shared by the Visualizer and the Drift
  Detector so the overview and the diagnostics cannot disagree about what
  a file inherits. All 200 pre-existing tests passed unchanged afterward,
  including the fixture's exact DJANGO006 count (the fixture assigns every
  risky value in its prod-like files directly, so nothing new fires there).
- 15 new tests (215 total).

Verification notes (worth recording because they caught real mistakes):
- My first manual check of the new diagnostics reported "no diagnostics
  anywhere" — which looked like success but was vacuous: shell `$'...'`
  quoting doesn't work in this `/bin/sh` (dash), so the test files were
  garbage, and my "clean files" list was computed from my own expected
  list rather than from what was actually scanned. Caught because the
  pre-existing own-file rule *also* reported nothing for a file that
  obviously should trigger it. Redone writing files from Python and
  asserting every file was discovered and parsed before reading any
  result; the new tests carry the same guard so they cannot pass
  vacuously.
- Mutation-tested the new tests rather than trusting them. Disabling the
  feature makes exactly the 6 positive tests fail. My first attempt at an
  "over-eager" mutation was wrong (it made inheritance find *nothing*, so
  it re-broke positive tests and never exercised the negative guards — I
  only noticed because the guards I cared about weren't in the caught
  list). Three properly targeted mutations (non-literal falls through;
  parent beats own assignment; first-import-wins ordering) are each
  caught by the intended negative guards. That surfaced one missing test
  (diagnostic-side conditional assignment), now added.

## 0.21.0 — Settings Visualizer follows `from .base import *` inheritance

Fixed (a documented Known Limitation):
- `summarize_settings_files()` now follows module-level star-imports
  between settings files, adding `extends`, `effective`, and
  `inherited_from` to each entry. The original own-file keys
  (`debug`, `secret_key_hardcoded`, `allowed_hosts_wildcard`) are
  unchanged, so every existing consumer and test is unaffected.
- Semantics chosen deliberately: chains resolve through intermediate
  files and are attributed to the file that actually set the value; a
  later `from .b import *` overrides an earlier `from .a import *`
  (Python's real order); an import cycle terminates via a visited set.
  Crucially, "not assigned here" is distinguished from "assigned to
  something unreadable" — `DEBUG = compute_debug()` or an assignment
  inside an `if` block stays *unknown* rather than falling back to the
  parent's value, since silently reporting the parent's `True` for a
  setting the file explicitly overrode would be a confident wrong answer.
- CLI `settings-overview` text and the VS Code webview now show effective
  values with an "(inherited from base)" / "from base" tag and an
  "extends" tag. TS bridge fields are optional so output from an older
  CLI still type-checks and falls back to own-file values.
- Manually verified on the real fixture and on override / non-literal /
  conditional / multi-level chain / cycle scenarios before writing tests.
  `tsc --noEmit` stub check: error counts identical to the prior run
  (11/32, both known stub-artifact categories) — no new errors.
- 13 new tests (200 total), including one confirming no secret value
  leaks into the new fields.
- Scope note: this changes the Visualizer only. The Drift Detector
  (DJANGO006) still inspects each file's own assignments, so DEBUG=True
  reaching production purely by inheritance is visible in the overview
  but not yet raised as a diagnostic — documented, not hidden.

## 0.20.0 — Gap fixes: deeper app discovery, abstract-base model fields

Fixed (two documented Known Limitations):
- **App discovery now descends up to 3 extra levels** under `src/`/`apps/`
  (previously exactly one), so `src/myproject/orders/models.py`-style
  layouts are found. `_scan_for_apps_recursive()` deliberately does NOT
  descend into a directory it already identified as an app, so a stray
  `models.py`-bearing subfolder inside an app can't spawn a phantom
  second app; non-app intermediate directories (`common/utils/`) are never
  reported. 3 new tests.
- **Admin UX Analyzer now follows abstract base classes** defined in the
  same `models.py` (`class Meta: abstract = True`), including multiple
  levels deep — base fields first, then the subclass's own, matching
  Django's real field ordering. This makes the very common
  `TimestampedModel` pattern work: `readonly_fields` is now correctly
  suggested for `created_at`/`updated_at` inherited from an abstract base.
  Concrete (multi-table inheritance) bases are deliberately NOT followed
  — their fields live on a separate table, so following them would
  misreport the child — and a visited-set guards against cyclic
  inheritance. A base in a different file is still not followed
  (documented). 6 new tests, including the multi-table and cyclic cases.
- Both fixes were manually verified against purpose-built scenarios
  (including the negative cases) before any tests were written; the
  existing fixture was confirmed unaffected both times.

Process note: the working container was reset twice during this round.
The first reset lost the (not-yet-checkpointed) discovery.py work, which
was redone from the last saved zip; since then the project is
checkpointed to `/mnt/user-data/outputs/djangolens.zip` immediately after
every passing change, before any documentation work. 9 new tests (187
total).

## 0.19.0 — API Contract Drift: resolve ModelSerializer's Meta.fields

Added:
- `analyzers/api_diff.py`'s `extract_serializers()` now resolves
  `ModelSerializer`'s `Meta.fields = [...]` / `Meta.fields = "__all__"`
  against the actual model — closing a previously-documented gap where a
  serializer with no explicit `Field()` declarations (the common
  `ModelSerializer` pattern) was entirely invisible to comparison. New
  `_resolve_meta_fields()` parses the model source and maps requested
  field names to their real types, with a `required` heuristic
  (`null=True`/`blank=True`/`default=` all mean "not required") — the
  same philosophy used elsewhere in this project, not a new one invented
  for this.
- Critical correctness point, designed in before writing any code: the
  model source must be fetched at the SAME git ref as the serializer
  being parsed on that side of the diff — not just read from the current
  working tree — or a model that changed between revisions would silently
  compare against the wrong state. `diff_serializers_file()` now builds
  one fetcher per ref (`_model_fetcher(base_ref)` / `_model_fetcher
  (head_ref)`), each resolving the model path relative to the
  serializer's own directory (same assumption the Admin UX Analyzer
  makes) and calling `_git_show()` for a committed ref or reading disk
  directly for the working tree.
- An explicit `Field()` declaration in the serializer still always
  overrides a Meta-resolved one, matching DRF's own override behavior —
  verified by a dedicated test.
- Manually verified against a real constructed git repo *before* writing
  any tests: a `ModelSerializer` with zero explicit fields, where the
  model's `email` field changes type (EmailField -> CharField) and a new
  required `phone` field is added between commits — both correctly
  detected, matching the hand-traced expectation exactly on the first run.
- 14 new tests (178 total): a dedicated `ResolveMetaFieldsTests` class
  unit-testing the core resolution function in isolation (no git needed),
  plus end-to-end git-repo tests for the full pipeline.
- Caught and fixed one bug in my own test code before running anything:
  a wrong constant name (`_MODEL_SERIALIZER_MODEL_V1_SOURCE` vs. the
  actual `_MODEL_SERIALIZER_MODEL_V1`) and a needlessly convoluted
  self-referential test — found and simplified during review, not left
  for the test run to surface.

## 0.18.0 — Admin UX: ChoiceField-aware list_filter

Added:
- `analyzers/admin.py`'s `list_filter` check now also fires for any field
  with a `choices=` kwarg, regardless of the field's own type (previously
  only `BooleanField`/`ForeignKey`/`ManyToManyField` — a documented gap
  from the last two rounds). `_find_model_fields()` extended once more to
  report each field's `has_choices` status.
- Manually verified against a purpose-built model (a `choices=` CharField
  alongside a plain IntegerField) before writing tests — correctly
  suggested the choices field and excluded the plain one on the first
  try. Confirmed the main fixture is unaffected (no `choices=` fields
  there) before touching any existing tests.
- 1 new test (164 total). Closed out the corresponding Known Limitations
  entry in the README rather than leaving it stale.

## 0.17.0 — Signal chain visualization

Added:
- `analyzers/signals.py`: `find_signal_chains()` / `scan_signal_chains()`
  return EVERY receiver found (both `@receiver`-decorated and
  `.connect()`-registered, including lambdas) as structured
  `{signal, sender, receiver, file, line, effects}` records — unlike
  `scan_signals()`, which only reports risky receivers as diagnostics.
  `effects` reuses the existing detection helpers (`recursive_save`,
  `network_call`, `email`), so the two views can never disagree about
  what counts as risky.
- New `django-doctor signal-chains` CLI command (text/JSON, not a
  diagnostics command).
- `src/signalChainsWebview.ts` + `runSignalChains()` bridge function: a
  "DjangoLens: Show Signal Chains" webview rendering the chains as a
  sender -> signal -> receiver -> effects diagram, risky chains
  red-bordered.
- Manually verified against the real fixture's signals.py before writing
  tests: all 4 receivers found with the right effects (the one safe
  receiver correctly shows none) — no bug this round.
- 9 new tests (163 total). `tsc --noEmit` stub check: only the two known
  stub-artifact error categories, no new ones.
- Honest scope note: this is one hop per receiver, not the transitive
  multi-hop chain from the original spec's mockup (a receiver's
  `.save()` triggering *another* model's signal receivers) — that needs
  resolving which model a `.save()` targets, which static analysis can't
  reliably do. Documented under README "Deferred".

Also this round: the working container was reset mid-session, losing the
in-progress project directory; restored from the previously saved
`djangolens.zip` (149/149 tests passed on the restored copy before any
new work resumed), and checkpoint zips are now saved more frequently.

## 0.16.0 — Admin UX Analyzer: autocomplete_fields

Added:
- `analyzers/admin.py` now flags missing `autocomplete_fields` too — the
  last of the six spec suggestions, and the only one needing
  whole-project context: a relational field is only suggested when its
  *target model* has its own `ModelAdmin` registered somewhere in the
  project (any app, not just the current file). `scan_admin()` now does
  two passes: first collecting every app's registered model names
  (`_find_registered_model_names()`), then analyzing each admin.py with
  that set available. `analyze_admin_file()` gained an optional
  `registered_model_names` parameter (defaults to empty, so calling it
  standalone on one file — as most of the existing tests do — correctly
  never fabricates a suggestion it has no evidence for).
- `_find_model_fields()` extended again to also report each relational
  field's target model name (`_relation_target_model()`), handling
  `ForeignKey(Model, ...)`, `ForeignKey('app.Model', ...)`, and
  `ForeignKey(to=..., ...)` forms, and correctly excluding `'self'`
  (a self-referential FK has no separate related admin to link to).
- Manually verified against the real fixture before writing tests (per
  this project's established discipline): confirmed `PaymentAdmin` gets
  suggested `autocomplete_fields = ('order',)` (Order has its own
  registered admin) while `OrderAdmin` does NOT get one for `customer`
  (User has no admin.py in the fixture at all) — matched the hand-traced
  expectation exactly, no bug found this round.
- 5 new tests (154 total), including one proving `analyze_admin_file()`
  alone never sees other files' registrations, and one proving a
  self-referential FK is never suggested even when trivially "registered."
- Consolidated a now-redundant Known Limitations entry in the README
  (the `list_filter`/`ChoiceField` gap was documented twice after this
  round's edits; merged into one).

## 0.15.0 — Admin UX Analyzer: search_fields, date_hierarchy, readonly_fields

Added:
- `analyzers/admin.py` now flags three more missing `ModelAdmin`
  attributes, each gated on a real matching field type so nothing is
  suggested out of thin air: `search_fields` (CharField/TextField/
  EmailField/SlugField/URLField), `date_hierarchy` (first DateField/
  DateTimeField in source order — suggests a string, not a tuple, unlike
  the others), and `readonly_fields` (fields with `auto_now=True` or
  `auto_now_add=True`). `_find_model_fields()` extended to also report
  each field's auto-timestamp status.
- Manually verified against a purpose-built model (text + date +
  auto_now/auto_now_add fields) before writing any tests, per this
  project's usual discipline — all three checks behaved correctly on the
  first try this round (no new bug found, unlike the last two admin
  changes).
- Two existing tests broke as an expected side effect (both used a bare
  `CharField`, now also a `search_fields` candidate) and were fixed by
  switching their fixtures to non-text field types where the test's
  actual point didn't depend on text fields.
- 7 net new tests (149 total).

## 0.14.0 — API Contract Drift VS Code entry point

Added:
- `src/apiDiffWebview.ts` + `runApiDiff()` bridge function: "DjangoLens:
  Compare API Contract" command prompts for a base git ref (and
  optionally a head ref, defaulting to the current working tree), then
  shows the same field-drift diagnostics as `api-diff` in a webview —
  following the exact pattern already established by the Settings
  Overview and Runtime Report webviews, since the backend was already
  fully built and tested.
- Re-verified with the `tsc --noEmit` stub smoke test; only the two known
  stub-artifact error categories, no new ones. Python test suite
  unaffected (144 tests, no backend changes this round).

This closes out VS Code entry points for every CLI feature except the
deliberately-not-built "Why is this slow?" one-click trigger (would
require executing the user's project from the extension) and signal
chain visualization.

## 0.13.0 — Admin list_filter suggestions + Runtime Report webview

Added:
- Admin UX Analyzer now also flags missing `list_filter` when the model
  has a real `BooleanField`/`ForeignKey`/`ManyToManyField` candidate,
  suggesting real field names. Caught and fixed a real bug via manual
  verification before writing tests: the initial single-field suggestion
  produced `list_filter = ('customer')`, which is just a parenthesized
  string, not a tuple (needs a trailing comma). Added
  `_format_str_tuple()` to handle this correctly for both `list_display`
  and `list_filter`, plus a regression test that actually parses the
  suggested fix and asserts it's an `ast.Tuple`, not just "valid Python".
  3 new tests (144 total).
- `src/runtimeReportWebview.ts` + `runRuntimeReport()` bridge function:
  "DjangoLens: Show Runtime Report" command reads `djangolens_runtime`'s
  JSON output into a sorted-slowest-first table with each endpoint's
  query count and top duplicate-query issue — closes the display half of
  the spec's "Why is this slow?" feature. Explicitly does NOT trigger
  Django execution itself; it only reads already-captured reports,
  staying on the same static/read-only side of the trust boundary as
  everything else in the extension.
- Re-verified with the `tsc --noEmit` stub smoke test; only the two known
  stub-artifact error categories, no new ones.

## 0.12.0 — Health score, Test Impact command, Settings Visualizer

Added:
- `health_score.py`: transparent 0-100 score with a documented formula
  (severity/confidence-weighted deductions, capped per rule ID). Computed
  on every `scan`, shown in the CLI text summary and `--format json`'s
  `health` key, and surfaced as a header item (with a tooltip breakdown)
  in the VS Code Issues tree. 11 new tests.
- `src/settingsWebview.ts` + `summarize_settings_files()` (in
  `analyzers/settings.py`): a "Settings Overview" webview panel showing
  every settings file's current DEBUG/SECRET_KEY/ALLOWED_HOSTS state —
  never secret values — distinct from the Settings Drift Detector, which
  only flags risks rather than showing the full picture. New
  `django-doctor settings-overview` CLI command. 6 new tests, verified
  against the real fixture (manual check before writing tests, per this
  project's usual discipline) before finalizing.
- `djangolens.analyzeTestImpact` VS Code command: right-click a Python
  file (editor or Explorer context menu) or run from the Command
  Palette; shows affected test modules (confidence + hop count) in a
  Quick Pick, selecting one opens it. New `runTestImpactScan()` bridge
  function in `pythonBridge.ts`.
- Re-verified with the `tsc --noEmit` stub smoke test across all
  new/changed files — only the two known stub-artifact error categories,
  no new ones.
- 17 new tests total (141 tests).

Fixed during this round (caught via immediate re-verification, not left
in): two `cli.py` edits and one `test_settings.py` edit each accidentally
merged a `def` line with its body onto one line, breaking syntax — caught
by re-parsing/re-running tests immediately after each edit rather than
batching edits before checking.

## 0.11.0 — API Contract Drift Detector

Added:
- API Contract Drift Detector (`DJANGO008`, `analyzers/api_diff.py`):
  compares DRF serializer field declarations between two git revisions
  (`git show` + `ast` — no Django/DRF import). Flags removed fields and
  type changes as `error` (breaking), a field becoming required as
  `warning`, and additions/fields-becoming-optional as `information`.
  New `django-doctor api-diff <path> --base <ref> [--head <ref>]` command,
  supporting `--format sarif` and `--strict` like the other diagnostics
  commands.
- Verified end-to-end against **real git repositories** (not mocked): the
  test suite actually runs `git init`/`git add`/`git commit` and diffs
  real commits, not fabricated data.
- Caught and fixed a real bug via manual verification before finalizing:
  `_find_serializer_files` originally relied on `discover_apps()` (which
  requires `models.py`/`apps.py`), silently missing a common real-world
  DRF pattern — a serializer-only "api" app with no `models.py` of its
  own. Fixed by having it walk the project directly. A regression test
  (`test_scan_api_diff_finds_serializer_only_app_with_no_models_py`)
  covers exactly this case.
- 17 new tests (123 total).

## 0.10.0 — ORM Query Detective (runtime companion package)

Added:
- `python/djangolens_runtime/`: a genuinely separate package (install
  target is the *user's own Django project*, not DjangoLens' environment)
  implementing the spec's "ORM Query Detective":
  - `frames.py` — pure-Python stack-frame filtering (maps a captured
    Python stack back to the innermost project-code frame, skipping
    Django/site-packages internals). No Django dependency; unit-tested.
  - `query_analysis.py` — pure-Python duplicate-query grouping (the
    runtime signature of N+1): groups by normalized SQL *and* source
    location, so coincidentally-identical queries from different call
    sites aren't conflated. No Django dependency; unit-tested.
  - `instrumentation.py` — `QueryCollector`, capturing every SQL query via
    `connection.execute_wrapper` (works regardless of `DEBUG`). Requires
    Django; **unverified** — this build environment has no Django
    installed, so this has never actually run. Written directly against
    Django's documented API.
  - `middleware.py` — `DjangoLensQueryMiddleware`, opt-in per-request
    capture writing one JSON report per request to disk. Also requires
    Django and is also unverified. Never sends anything over the network.
  - `README.md` — install/enable instructions, explicit about what's
    tested vs. not.
- `python/django_doctor/runtime_report.py` (Django-free, tested): reads
  and summarizes the JSON reports the middleware writes. New
  `django-doctor runtime-report <dir>` CLI command.
- 16 new tests (106 total): `test_frames.py`, `test_query_analysis.py`
  (both exercise the real Django-free logic directly), and
  `test_runtime_report.py` (against fabricated JSON reports simulating
  exactly what the middleware would produce).

This is the one feature in the whole project that crosses into "requires
executing the user's Django project" territory — everything else remains
100% static (`ast.parse()` only). See ARCHITECTURE.md "Trust boundary".

## 0.9.0 — Test Impact Analyzer

Added:
- Test Impact Analyzer (`analyzers/test_impact.py`): given a changed file,
  builds a project-wide static import graph (extending the same
  `iter_python_files`/`ast` approach used elsewhere) and reverse-walks it
  breadth-first to find test modules that likely need re-running —
  `high` confidence for a direct importer, `likely` for anything found
  through further hops. New `django-doctor test-impact <path>
  <changed_file>` CLI command (text/JSON, no SARIF — not diagnostics).
- Fixture: 3 new test files (`orders/tests/test_models.py`,
  `orders/tests/test_views.py`, `payments/tests/test_payment.py`) forming
  a real direct + 2-hop same-app + 2-hop cross-app import chain, reusing
  existing fixture relationships rather than inventing new coupling.
- Caught and fixed a real bug immediately via manual verification before
  writing tests: a relative-import depth error (`from .indexer import
  ...` should have been `from ..indexer import ...`, since the new module
  lives one package level deeper).
- 9 new tests (81 total).

## 0.8.0 — SARIF output + GitHub Actions template

Added:
- `format_sarif()` in `cli.py`: SARIF 2.1.0 output on every diagnostics
  command (`scan`, `migrations`, `signals`, `settings`, `admin`) via
  `--format sarif`. Relative, forward-slashed artifact URIs; stable
  per-rule descriptions (not tied to whichever instance appeared first);
  severity mapped to SARIF's error/warning/note levels.
- `examples/github-actions/djangolens.yml`: a copy-paste CI template that
  uploads SARIF results to GitHub code scanning and gates PRs on
  `django-doctor migrations --strict`. Checks out DjangoLens as a second
  repo, since it isn't published to PyPI — documented as a known
  limitation, along with the fact that this template has only been
  YAML-validated, not run end-to-end (no network access to GitHub here).
- 7 new tests (72 total), including an actual subprocess round-trip
  through the real CLI confirming the SARIF output is valid JSON.

## 0.7.0 — Gap fixes, full VS Code UI, Admin UX Analyzer

Gap fixes:
- App discovery now finds apps nested one level under `src/` or `apps/`
  at the project root, not just flat-layout apps.
- N+1 Code Action (and the new hover/CodeLens below) now correlate to the
  raw diagnostic by file + line + **column**, not just file + line —
  closes the "two flagged relations on one line" ambiguity.

New VS Code UI:
- `src/hoverProvider.ts`: structured Markdown hover (rule ID, confidence,
  explanation, suggested fix) on every flagged token.
- `src/codeLensProvider.ts`: an inline lens above every flagged line;
  clicking shows the full diagnostic detail via a new
  `djangolens.showDiagnosticDetail` command.
- `src/issuesTreeProvider.ts` + a dedicated **Activity Bar icon/container**
  (`resources/icon.svg`, monochrome/currentColor): a new "Issues" sidebar
  view listing every diagnostic from the last scan, grouped by severity.
  The existing "URLs" view moved into this same container (out of the
  plain Explorer sidebar).

New analyzer:
- Admin UX Analyzer (`DJANGO007`): flags `ModelAdmin` classes (both
  `@admin.register` and `admin.site.register` forms) with no
  `list_display`, and suggests one using the model's real fields parsed
  from `models.py` — not a generic placeholder. New `django-doctor admin`
  CLI command.

10 new tests (65 total). Re-verified with the `tsc --noEmit` stub smoke
test across all new/changed TypeScript files — only the same two known
stub-artifact error categories, no new ones.

## 0.6.0 — N+1 quick fix (Code Action)

Added:
- `n1_detector.py` now attaches structured `relation`/`insert_line`/
  `insert_col` fields to each DJANGO003 diagnostic — the exact, always-safe
  position (end of the queryset expression) to append
  `.select_related(...)`/`.prefetch_related(...)`.
- `src/codeActionProvider.ts`: a real VS Code Code Action (lightbulb quick
  fix) for N+1 diagnostics, offering both `.select_related(...)` and
  `.prefetch_related(...)` as separate choices rather than guessing which
  one is correct — that depends on the relation's actual direction
  (forward FK/O2O vs reverse/M2M), which static analysis can't determine
  without executing the Django app registry.
- New test: splices the suggested fix into real source at the computed
  position and confirms the result is both valid Python and no longer
  flagged on re-scan (55 tests total).
- Re-verified with the `tsc --noEmit` stub smoke test after the new file;
  no new error categories.

## 0.5.0 — URL Explorer sidebar view

Added:
- `src/urlsTreeProvider.ts`: a `vscode.TreeDataProvider` for a new
  "Django URLs" view in the Explorer sidebar, backed by the existing
  `discover_urls()` Python logic (via a new `runUrlsScan()` bridge
  function). Endpoints are grouped by first path segment; clicking one
  opens its `path()`/`re_path()` call at the right line. A
  `FileSystemWatcher` on `**/urls.py` triggers automatic refresh, plus a
  manual refresh command/icon in the view's title bar.
- `pythonBridge.ts` refactored to share subprocess-spawning logic
  (`runCli<T>()`) between `runScan()` and the new `runUrlsScan()`.
- Verified with `tsc --noEmit` against a stub `vscode`/Node-builtin module
  (no network access to install `@types/vscode` in this environment) — a
  syntax/reference smoke test, not a full type-check against the real API.

## 0.4.0 — URL Explorer + gap fixes

Added:
- URL Explorer: resolves `urlpatterns` from `ROOT_URLCONF` through nested
  `include(...)` calls (string-module targets) into a flat,
  prefix-concatenated endpoint list with view/name/location. New
  `django-doctor urls` command (not part of `scan` — it's navigation data,
  not diagnostics).
- Fixed: Migration Risk Analyzer now orders each app's migrations using
  their actual `dependencies = [...]` graph (topological sort) instead of
  filename numbering.
- Fixed: circular-dependency diagnostics (`DJANGO001`) now attach a
  representative import statement's file/line, so they render in the
  Problems panel like any other diagnostic instead of only appearing in
  CLI/JSON output.
- 10 new tests (54 total).

## 0.3.0 — Settings Drift Detector

Added:
- Settings Drift Detector (`DJANGO006`): DEBUG=True in production-like
  environment files, hardcoded SECRET_KEY (single-file and cross-file),
  wildcarded ALLOWED_HOSTS in production-like settings. Never reads secret
  values into a diagnostic. New `django-doctor settings` CLI command.
- 11 new tests (44 total), including a 4-file settings/ fixture
  (base/development/staging/production) and an explicit test asserting no
  secret value ever appears in diagnostic output.

## 0.2.1 — Gap fixes

- Migration Risk Analyzer now tracks field nullability across an app's
  migration sequence (`CreateModel`→`AddField`→`AlterField`), so
  `AlterField(null=False)` is only flagged as a regression when an earlier
  migration actually shows `null=True`. Removes a false-positive class for
  fields that were already non-nullable; falls back to explicit
  `heuristic`-confidence flagging when history genuinely can't be
  determined, instead of silently guessing at `static` confidence.
- Signal Detector now also resolves `signal.connect(handler, sender=Model)`
  registrations (both a named function and an inline `lambda`), not just
  `@receiver(...)`-decorated functions.
- 6 new tests (33 total).

## 0.2.0 — Migration Risk Analyzer + Signal Detector

Added:
- Migration Risk Analyzer (`DJANGO004`): destructive operations, irreversible
  RunPython/RunSQL, risky AddField/AlterField nullability, AddIndex locking
  risk. New `django-doctor migrations` CLI command.
- Signal Detector (`DJANGO005`): recursive post_save/pre_save `.save()`
  calls, synchronous network/email calls inside `@receiver` handlers. New
  `django-doctor signals` CLI command.
- 14 new tests (27 total), including a migration-fixture set covering both
  risky and safe operations, and a signals fixture with a safe receiver
  alongside two flagged ones.

## 0.1.0 — Foundation

Initial working foundation, not a full release of the original 11-analyzer
spec. See README.md → "Implemented vs. deferred" for the full breakdown.

Added:
- Project/app discovery
- Cross-app import indexing (`ast`-based)
- Architecture Linter: circular dependency detection (`DJANGO001`) and
  configurable allow/deny rules (`DJANGOARCH`)
- Possible N+1 query detector (`DJANGO003`)
- VS Code diagnostics integration (Problems panel)
- Standalone `django-doctor` CLI (text/JSON, `--strict` mode)
- Test suite (13 tests, stdlib `unittest`, no external dependencies)
