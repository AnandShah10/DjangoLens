# Contributing to DjangoLens

Thank you for helping improve DjangoLens for the Django and VS Code developer community.

**Made with love by Anand Shah for Developer Community**

## Development setup

### Extension (TypeScript)

```bash
npm install
npm run compile
# or watch mode
npm run watch
```

Press **F5** in VS Code to open the Extension Development Host.

### Python analyzers & CLI

```bash
cd python
python -m pytest tests/ --ignore=tests/fixtures -q
python -m django_doctor.cli scan tests/fixtures/sample_project
```

No Django install is required for the static analyzer unit tests. Fixture projects under `tests/fixtures/` import Django and are excluded from default pytest collection.

## Project layout

- `src/` — VS Code extension (TypeScript)
- `python/django_doctor/` — static analysis engine + CLI
- `python/djangolens_runtime/` — optional Django middleware for SQL capture
- `python/tests/` — unit tests and sample Django fixtures
- `examples/github-actions/` — CI template (SARIF)

## Adding an analyzer

1. Prefer implementing against the patterns in `python/django_doctor/analyzers/`.
2. Use the formal protocol in `analyzers/base.py` when registering new analyzers.
3. Emit diagnostics with: `rule_id`, `analyzer`, `severity`, `confidence`, `message`, `explanation`, `suggested_fix`, and source location fields when available.
4. Wire the analyzer into `cli.py` `run_scan` and add a focused subcommand if useful.
5. Add true-positive and false-positive tests under `python/tests/`.
6. Document the rule ID in `README.md` (rule reference table) and `CHANGELOG.md`.

## Coding guidelines

- Prefer accurate, low-noise diagnostics over broad heuristics.
- Label confidence honestly: `high`, `static`, or `heuristic`.
- Never execute arbitrary project code during normal static analysis.
- Never log or display secret values (`SECRET_KEY`, credentials, tokens).
- Keep the Python engine free of a hard Django dependency for static scans.

## Pull requests

- Keep PRs focused; include tests for new rules.
- Update `CHANGELOG.md` under a new version section when user-facing behavior changes.
- Ensure `python -m pytest tests/ --ignore=tests/fixtures` passes.

## Code of conduct

Be respectful and constructive. This project exists to help developers ship safer Django applications.
