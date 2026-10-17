# Developer conveniences. `make ci` runs the same checks as
# .github/workflows/test.yml, in the same order, so a green run here is a
# green run there; CONTRIBUTING.md documents the gate.

# Headless Chrome/Chromium renders docs/catalog-banner.html to the PNG the
# plugin-catalog card shows (2:1, 1200x600). Override CHROME if autodetection
# misses your binary, SCALE for a 2x render (2400x1200, also 2:1).
CHROME ?= $(shell command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || command -v chromium-browser 2>/dev/null || command -v chrome 2>/dev/null || ls "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" 2>/dev/null)
SCALE  ?= 1

.PHONY: banner
banner:
	@test -n "$(CHROME)" || { echo "no Chrome/Chromium found; set CHROME=/path/to/chrome"; exit 1; }
	"$(CHROME)" --headless=new --disable-gpu --hide-scrollbars \
	  --force-device-scale-factor=$(SCALE) --window-size=1200,600 \
	  --screenshot="$(abspath docs/catalog-banner.png)" "file://$(abspath docs/catalog-banner.html)" >/dev/null 2>&1
	@python3 -c 'import struct,sys; d=open("docs/catalog-banner.png","rb").read(24); w,h=struct.unpack(">II", d[16:24]); print("docs/catalog-banner.png:", w, "x", h, "px,", len(open("docs/catalog-banner.png","rb").read())//1024, "KB"); sys.exit(0 if w==2*h else 1)'

# ── Live before/after check for refactor PRs (#410) ───────────────────
# Runs the scenario harness's cli turns through a real Hermes twice, once with BASE's
# package installed in the profile's plugin dir and once with the working
# tree's, scores every telemetry source with scenarios/check.py, and diffs the
# shape of what landed in the live store. Needs the profile (default
# `minimal`) with the plugin installed, a model it can call and the backend
# in its hermes_otel.yaml answering. ~5 min for four prompts × two sides.
BASE ?= main
PROFILE ?= minimal
.PHONY: refactor-check
refactor-check:
	uv run --extra dev python scripts/refactor_check/refactor_check.py --base $(BASE) --profile $(PROFILE)

# ── Scenario harness (#278) ────────────────────────────────────────────
# Real Hermes runs with telemetry expectations (scripts/scenarios/README.md).
# Both targets refresh the harness profile from this checkout (model from
# MODEL_FROM's config.yaml, backends from BACKENDS_FROM's hermes_otel.yaml;
# BACKENDS_FROM= for the live store only) and check every source; a source
# that is down is skipped. `make scenarios`: the quick suite (cli + resume
# turns), a few minutes. `make scenarios-full`: every mode (gateway,
# approvals, cron, kanban across two profiles; SECOND_MODEL_ARGS gives the
# second profile its own model). ONLY=id,id runs just those scenarios;
# SCENARIO_ARGS passes more flags to run.py.
SCENARIO_PROFILE ?= scenarios
SCENARIO_PROFILE_B ?= scenarios-b
MODEL_FROM ?= default
BACKENDS_FROM ?= default
SECOND_MODEL_ARGS ?=
ONLY ?=
SCENARIO_ARGS ?=
SCENARIO_TOKEN := S$(shell date +%y%m%d-%H%M%S)
SCENARIO_RUNS = $(or $(HERMES_HOME),$(HOME)/.hermes)/profiles/$(SCENARIO_PROFILE)/scenario_runs/$(SCENARIO_TOKEN)
.PHONY: scenarios scenarios-full
scenarios:
	uv run --extra dev python scripts/scenarios/setup_profile.py --profile $(SCENARIO_PROFILE) --model-from $(MODEL_FROM) $(if $(BACKENDS_FROM),--backends-from $(BACKENDS_FROM))
	-uv run --extra dev python scripts/scenarios/run.py --profile $(SCENARIO_PROFILE) --suite quick --token $(SCENARIO_TOKEN) $(if $(ONLY),--only $(ONLY)) $(SCENARIO_ARGS)
	uv run --extra dev python scripts/scenarios/check.py --manifest $(SCENARIO_RUNS)/runs.json

scenarios-full:
	uv run --extra dev python scripts/scenarios/setup_profile.py --profile $(SCENARIO_PROFILE) --model-from $(MODEL_FROM) $(if $(BACKENDS_FROM),--backends-from $(BACKENDS_FROM)) --second-profile $(SCENARIO_PROFILE_B) $(SECOND_MODEL_ARGS)
	-uv run --extra dev python scripts/scenarios/run.py --profile $(SCENARIO_PROFILE) --suite full --token $(SCENARIO_TOKEN) $(if $(ONLY),--only $(ONLY)) $(SCENARIO_ARGS)
	uv run --extra dev python scripts/scenarios/check.py --manifest $(SCENARIO_RUNS)/runs.json

# ── The CI gate, locally ───────────────────────────────────────────────
# Mirrors .github/workflows/test.yml job for job: lint (lockfile, ruff,
# black), plugin scan, tests with the 85% coverage gate and the per-module
# floors (coverage-floors.json), dashboard bundle up to date +
# tsc/eslint/prettier + vitest, docs site build, wheel build. `make ci-fast`
# skips the two npm builds when you did not touch dashboard-ui/ or website/.
#
# Not run by `make ci` (slow or environment-bound): the Python 3.9 / 3.11
# matrix (this runs the current venv's interpreter only), `make ci-floor`
# (the suite against the OpenTelemetry 1.35 floor, a fresh venv), and the
# catalog-admission workflow (installs hermes-agent main; push to main,
# weekly, or dispatch).
.PHONY: ci ci-fast ci-lint ci-scan ci-semconv ci-test ci-floor ci-dashboard ci-docs ci-wheel
ci: ci-lint ci-scan ci-semconv ci-test ci-dashboard ci-docs ci-wheel
	@echo "✓ ci: every check that GitHub Actions runs passed locally"

ci-fast: ci-lint ci-scan ci-semconv ci-test
	@echo "✓ ci-fast: lint, scan and tests passed (dashboard/docs/wheel skipped)"

ci-lint:
	uv lock --check
	uv run --extra dev ruff check .
	uv run --extra dev black --check .

ci-scan:
	uv run --extra dev python scripts/scan_plugin_artifact.py

# The telemetry vocabulary (hermes_otel/semconv.py) generates
# dashboard-ui/src/semconv.generated.ts and the tables of the span attribute
# reference page; a stale file or a drifted table fails here (#404, #523).
ci-semconv:
	uv run python scripts/gen_semconv.py --check

ci-test:
	uv run --extra dev pytest --cov=hermes_otel --cov-branch --cov-report=term --cov-report=json --cov-fail-under=85 -q
	uv run python scripts/coverage_floor.py

# The declared OpenTelemetry floor, in a fresh venv (what the test-otel-floor
# job runs). Slow (~2 min); run it when touching the OTel SDK surface.
OTEL_FLOOR ?= 1.35.0
ci-floor:
	uv venv --quiet --python 3.11 .venv-floor
	uv pip install --quiet --python .venv-floor/bin/python -e ".[dev]" \
	  "opentelemetry-api==$(OTEL_FLOOR)" "opentelemetry-sdk==$(OTEL_FLOOR)" \
	  "opentelemetry-exporter-otlp-proto-http==$(OTEL_FLOOR)"
	.venv-floor/bin/python -c 'import sys, opentelemetry.sdk.version as v; assert v.__version__ == sys.argv[1], v.__version__; print("opentelemetry-sdk", v.__version__)' "$(OTEL_FLOOR)"
	.venv-floor/bin/python -m pytest --tb=short -q

# CI compares the committed bundle with a fresh build. Locally the bundle
# may be rebuilt but not yet committed, so compare the working tree's bundle
# with what a fresh build produces: a change means it was stale.
ci-dashboard:
	@before=$$(cat hermes_otel/dashboard/dist/index.js hermes_otel/dashboard/dist/style.css | shasum); \
	(cd dashboard-ui && npm ci --silent && npm run build --silent) || exit 1; \
	after=$$(cat hermes_otel/dashboard/dist/index.js hermes_otel/dashboard/dist/style.css | shasum); \
	if [ "$$before" != "$$after" ]; then echo "dashboard bundle was stale and has been rebuilt: commit hermes_otel/dashboard/dist and rerun"; exit 1; fi; \
	echo "✓ dashboard bundle is up to date"
	cd dashboard-ui && npm run check --silent && npm test --silent

ci-docs:
	cd website && npm ci --silent && npm run build --silent

ci-wheel:
	rm -rf dist && uv build --quiet
	@listing=$$(unzip -l dist/*.whl); \
	for f in hermes_otel/plugin.yaml hermes_otel/dashboard/dist/index.js hermes_otel/skills/observability/SKILL.md hermes_otel/hooks/__init__.py hermes_otel/hooks/tools.py; do \
	  echo "$$listing" | grep -q "$$f" || { echo "missing from wheel: $$f"; exit 1; }; \
	done; echo "✓ wheel contains the plugin files"
