<!-- Start with "Closes #<issue>" so the issue auto-closes. -->

## What

<!-- What changed and why, in a few sentences. -->

## Checks

- [ ] `make ci` is green locally (lint, scan, suite with the 85 % gate and the per-module coverage floors, dashboard bundle, docs, wheel). `make ci-fast` when `dashboard-ui/` and `website/` are untouched.
- [ ] Conventional-commit type matches the change: `feat:` / `fix:` / `docs:` cut a release, `refactor:` / `test:` / `ci:` / `chore:` do not.
- [ ] Docs updated where behaviour, config or telemetry changed (`website/docs/reference/*`, `README.md`).

### Refactor milestone PRs (#414) only

- [ ] No golden fixture changed (`tests/golden/`), or every changed line is listed below with its reason.
- [ ] `make refactor-check` report attached when `hooks/`, `tracer.py`, `live_store.py`, `log_handler.py`, `dashboard/backends/` or `backends.py` changed.
- [ ] No feature work mixed in.
