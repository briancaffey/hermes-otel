# dashboard_verify

End-to-end verification of the dashboard tab against a real backend. The plan,
the check catalogue and the per-backend loop live in
`.claude/skills/hermes-otel-dashboard-verify/SKILL.md`; this directory holds the
driver that scores it.

```bash
uv run --with playwright playwright install chromium        # once

# run the four-turn workload, then score one backend
uv run --with playwright --extra dev python scripts/dashboard_verify/verify_dashboard.py \
    --source lgtm-local --profile minimal --run-batch

# re-score an earlier batch for several sources (no new turns)
uv run --with playwright --extra dev python scripts/dashboard_verify/verify_dashboard.py \
    --source lgtm-local --source openobserve-local --source live \
    --profile minimal --batch B261006-200043
```

- `verify_dashboard.py` — the driver. S/T/M/L checks go through the plugin
  API with the profile's live store as the oracle; B checks drive the real
  `/otel` tab in headless Chromium and compare the page with the API.
- `backends.yaml` — what each backend type must serve (metrics, logs, UI link,
  `status=error` semantics) and its known quirks.
- `out/<batch>/<source>/` — `report.md`, `report.json` and one screenshot per
  page (gitignored).

The workload is `scripts/turn_batch` (`shell-echo`, `no-tools`,
`missing-file`, `multi-tool` by default; `--prompts` picks others). The
dashboard must be running (`hermes dashboard --port 9119 --no-open`) and the
source must be a named entry in the profile's `hermes_otel.yaml`.
