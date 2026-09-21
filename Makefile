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

# ── The CI gate, locally ───────────────────────────────────────────────
# Mirrors .github/workflows/test.yml job for job: lint (lockfile, ruff,
# black), plugin scan, tests with the 85% coverage gate, dashboard bundle
# up to date + vitest, docs site build, wheel build. `make ci-fast` skips
# the two npm builds when you did not touch dashboard-ui/ or website/.
.PHONY: ci ci-fast ci-lint ci-scan ci-test ci-dashboard ci-docs ci-wheel
ci: ci-lint ci-scan ci-test ci-dashboard ci-docs ci-wheel
	@echo "✓ ci: every check that GitHub Actions runs passed locally"

ci-fast: ci-lint ci-scan ci-test
	@echo "✓ ci-fast: lint, scan and tests passed (dashboard/docs/wheel skipped)"

ci-lint:
	uv lock --check
	uv run --extra dev ruff check .
	uv run --extra dev black --check .

ci-scan:
	uv run --extra dev python scripts/scan_plugin_artifact.py

ci-test:
	uv run --extra dev pytest --cov=hermes_otel --cov-report=term --cov-fail-under=85 -q

# CI compares the committed bundle with a fresh build. Locally the bundle
# may be rebuilt but not yet committed, so compare the working tree's bundle
# with what a fresh build produces: a change means it was stale.
ci-dashboard:
	@before=$$(cat hermes_otel/dashboard/dist/index.js hermes_otel/dashboard/dist/style.css | shasum); \
	(cd dashboard-ui && npm ci --silent && npm run build --silent) || exit 1; \
	after=$$(cat hermes_otel/dashboard/dist/index.js hermes_otel/dashboard/dist/style.css | shasum); \
	if [ "$$before" != "$$after" ]; then echo "dashboard bundle was stale and has been rebuilt: commit hermes_otel/dashboard/dist and rerun"; exit 1; fi; \
	echo "✓ dashboard bundle is up to date"
	cd dashboard-ui && npm test --silent

ci-docs:
	cd website && npm ci --silent && npm run build --silent

ci-wheel:
	rm -rf dist && uv build --quiet
	@listing=$$(unzip -l dist/*.whl); \
	for f in hermes_otel/plugin.yaml hermes_otel/dashboard/dist/index.js hermes_otel/skills/observability/SKILL.md hermes_otel/hooks/__init__.py hermes_otel/hooks/tools.py; do \
	  echo "$$listing" | grep -q "$$f" || { echo "missing from wheel: $$f"; exit 1; }; \
	done; echo "✓ wheel contains the plugin files"
