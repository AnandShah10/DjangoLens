# Security and privacy

**Made with love by Anand Shah for Developer Community**

DjangoLens is a local developer tool. This document describes the privacy model and how to report security issues.

## Privacy model

| Data | How it is handled |
|------|-------------------|
| Source code | Read locally for AST/static analysis only |
| SQL queries | Captured only if you install and enable `djangolens_runtime` middleware; written to a directory you configure on disk |
| Environment variables / secrets | Not collected. Diagnostics may note that a `SECRET_KEY` is *hardcoded* without showing its value |
| Database credentials | Never read or transmitted by the extension |
| Telemetry | None. No analytics, no phone-home, no external API calls for analysis |

Static analysis **does not** import or execute your Django project.

Runtime instrumentation is **opt-in**, runs inside *your* process, and stores reports locally. The VS Code commands “Analyze Django Performance” and “Setup Runtime Instrumentation” only open setup docs or existing reports — they do not start your application for you.

## Trust boundaries

- The VS Code extension spawns a local Python process to run `django_doctor.cli` and parse JSON results.
- Configure the interpreter with `djangolens.pythonPath` if needed.
- Architecture rules and analyzer toggles live in workspace settings / `djangolens.config.json`.

## Reporting a vulnerability

If you discover a security issue (for example, secret leakage in diagnostics, path traversal in report loading, or unsafe command execution):

1. **Do not** open a public GitHub issue with exploit details.
2. Contact the maintainer privately via GitHub: [AnandShah10](https://github.com/AnandShah10).
3. Include steps to reproduce and affected versions.

We aim to acknowledge reports promptly and ship fixes as quickly as practical.

## Supported versions

Security fixes are applied on a best-effort basis to the latest release on the default branch. Pin a release tag in CI when you need a fixed artifact.

## Related documentation

- Runtime package: `python/djangolens_runtime/README.md`
- Architecture: `ARCHITECTURE.md`
