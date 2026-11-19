# DjangoLens rule reference

**Made with love by Anand Shah for Developer Community**

All diagnostics carry a machine-readable `rule_id`, severity, confidence, explanation, and optional suggested fix.

## Suppression

Inline (same line or line above):

```python
# django-doctor: ignore DJANGO003
for order in Order.objects.all():
    print(order.customer.name)
```

Multiple codes: `# django-doctor: ignore DJANGO003, DJANGO015`

Project config (`djangolens.config.json`):

```json
{
  "suppress": ["DJANGO012", "DJANGO015"],
  "analyzers": {
    "unused": false,
    "deprecated_apis": true
  }
}
```

## Rules

| ID | Analyzer | Severity | Confidence | Summary |
|----|----------|----------|------------|---------|
| DJANGO001 | architecture | error | high | Circular app dependency |
| DJANGOARCH | architecture | error | high | Allow/deny import rule violation |
| DJANGO003 | orm / n1 | warning | heuristic | Possible N+1 relationship access in loop |
| DJANGO004 | migrations | error/warning/info | static | Risky migration operation |
| DJANGO005 | signals | warning | heuristic | Recursive save or sync network/email in receiver |
| DJANGO006 | settings | error/warning | static | DEBUG / SECRET_KEY / ALLOWED_HOSTS drift |
| DJANGO007 | admin | information | heuristic | ModelAdmin missing useful options |
| DJANGO008 | api | error/warning/info | static | DRF serializer contract drift (git) |
| DJANGO009 | models_health | error/info | high/heuristic | on_delete issues |
| DJANGO010 | models_health | warning | high/heuristic | null/unique/blank mismatches |
| DJANGO011 | models_health | information | heuristic | Index / lookup field hints |
| DJANGO012 | unused | information | heuristic | Possibly unused model |
| DJANGO013 | unused | information | heuristic | Model missing admin registration |
| DJANGO014 | deprecated_apis | error/warning | high/heuristic | Deprecated Django API |
| DJANGO015 | unused_urls | information | heuristic | URL name never reverse()'d / {% url %} |
| DJANGO016 | unused_signals | information/warning | heuristic | Unregistered or broken signal handler |

## Confidence levels

- **high** — fact about the AST/import graph (e.g. a cycle exists)
- **static** — determined from files alone without runtime
- **heuristic** — pattern match; prefer false negatives over noise

## CLI

```bash
python -m django_doctor.cli scan . --config djangolens.config.json --format sarif --strict
```
