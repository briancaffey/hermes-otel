---
sidebar_position: 3
title: "Releasing"
description: "The release-please flow — commits on main drive version bumps and changelog entries automatically."
---

# Releasing

hermes-otel uses [release-please](https://github.com/googleapis/release-please) to automate releases. The flow is:

1. You merge conventional commits to `main`.
2. A `release-please` workflow opens (or updates) a Release PR that bumps the version and updates `CHANGELOG.md`.
3. When you merge that Release PR, a GitHub Release is cut and a tag is pushed.
4. (Optional) A follow-up workflow can publish to PyPI / rebuild the docs / etc.

## Conventional Commits → version bumps

release-please reads commit messages. The bump is determined automatically:

| Commit prefix | Bump |
|---|---|
| `fix:`, `perf:`, `docs:`, `deps:`, `revert:` | Patch (1.8.0 → 1.8.1) |
| `feat:` | Minor (1.8.0 → 1.9.0) |
| `feat!:` or `BREAKING CHANGE:` in body | Major (1.8.0 → 2.0.0) |
| `refactor:`, `chore:`, `ci:`, `test:`, `build:`, `style:` | No bump, no release PR (release-please logs "No user facing commits found") |

So `feat(backends): add honeycomb` → minor bump + changelog entry under "Features". `fix(hooks): avoid double-ending span` → patch bump + entry under "Bug Fixes". A docs-only merge still cuts a patch release (its entry lands under "Documentation").

The hidden types are excluded from the changelog *and* from the bump decision, so a `refactor:` that changes something users can observe (a new span attribute, a metric label) ships only with the next visible commit. Prefer a `fix:` / `feat:` title in that case, or add a `Release-As: X.Y.Z` footer to the commit body to force the release.

## The Release PR

After a qualifying commit lands, release-please opens a PR titled something like:

```text
chore(main): release hermes-otel 0.2.0
```

The PR updates:

- `pyproject.toml` version field (release type `python`)
- `hermes_otel/plugin.yaml` `version` — the number `hermes plugins list` shows (declared under `extra-files`)
- `CHANGELOG.md` with the commit subjects grouped by type
- `.release-please-manifest.json` (internal tracking file)

Review the changelog entries — if you want to tweak wording, edit the PR directly and re-merge.

## Configuration

Two files control release-please:

- `release-please-config.json`: release type, tag format, and the `extra-files` list (plugin.yaml)
- `.release-please-manifest.json`: current version

```json
// release-please-config.json (current)
{
  "packages": {
    ".": {
      "release-type": "python",
      "package-name": "hermes-otel",
      "include-v-in-tag": true,
      "extra-files": [
        { "type": "yaml", "path": "hermes_otel/plugin.yaml", "jsonpath": "$.version" }
      ]
    }
  }
}
```

Tags are `hermes-otel-vX.Y.Z` (the component name is part of the tag), and GitHub releases carry the same name.

## The workflow

`.github/workflows/release-please.yml` runs on every push to `main` and does three things:

1. Runs `googleapis/release-please-action`, which opens or updates the Release PR, or cuts the release when the Release PR was just merged.
2. While a Release PR is open, merges `main` into its branch and refreshes `uv.lock` (release-please bumps `pyproject.toml` but never the lockfile, and only rebases the branch when the release notes change), so the PR's checks stay current.
3. When a release was just created, renders the plugin-catalog entry for it (next section), uploads it to the GitHub release as `hermes-otel.yaml`, and prints it in the job summary.

### Why a PAT instead of `GITHUB_TOKEN`?

The `GH_PAT` secret is a personal access token with `contents: write` + `pull-requests: write`. It's used instead of `GITHUB_TOKEN` because:

- A Release PR is opened by `GITHUB_TOKEN` → GitHub doesn't trigger downstream workflows (CI) on that push (loop protection).
- With a PAT, the push *does* trigger CI — so the Release PR is checked before merge.

## After the release: bump the plugin-catalog pin

hermes-otel is listed in the [Hermes plugin catalog](https://github.com/NousResearch/hermes-agent/tree/main/plugin-catalog) (tracked in [issue #134](https://github.com/briancaffey/hermes-otel/issues/134) until the first entry is merged). The catalog pins an exact commit, so users on `hermes plugins install hermes-otel` only see a release once a **sha-bump PR** updates `sha` and `version` in `plugin-catalog/hermes-otel.yaml` upstream. Every release therefore ends with one more step:

1. Download `hermes-otel.yaml` from the GitHub release (or copy it from the release-please job summary). It is rendered by `scripts/render_catalog_entry.py` from the released commit's `plugin.yaml`, so its `sha`, `version`, `provides_hooks` and `requires_hermes` already match the tag. To render it by hand:

   ```bash
   python scripts/render_catalog_entry.py --sha "$(git rev-parse hermes-otel-vX.Y.Z)" --version X.Y.Z
   ```

2. In a fork of `NousResearch/hermes-agent`, replace `plugin-catalog/hermes-otel.yaml` with that file and open a PR titled `plugin-catalog: bump hermes-otel to X.Y.Z`. Upstream CI clones this repo at the pinned sha and runs `hermes plugins validate --install-deps`; the `Catalog admission checks` job in this repo's CI runs the same gates on every PR, so the upstream check should already be green.

3. Reviewers look at the commit range between the old and new sha, so keep the PR body to the release notes link and anything a reviewer must know (new hooks, new dependencies, new env vars).

Skip a release only if nothing in `hermes_otel/` changed; docs-only releases do not need a bump.

## Publishing to PyPI (future)

Not yet wired up. When it is:

1. Add a `release` trigger on tags to `.github/workflows/publish.yml`.
2. Build wheel + sdist with `uv build`.
3. Publish with `uv publish` or `twine` using a PyPI token stored as a secret.
4. Include trusted-publisher OIDC if PyPI accepts it for the org.

Open an issue if you'd like to see this sooner.

## Manual release

You should almost never need this; release-please handles everything. If the action is broken:

```bash
# Bump the version in pyproject.toml, hermes_otel/plugin.yaml and .release-please-manifest.json,
# then update CHANGELOG.md
git commit -am "chore(main): release hermes-otel X.Y.Z"
git tag hermes-otel-vX.Y.Z
git push origin main --tags
gh release create hermes-otel-vX.Y.Z --notes-from-tag
python scripts/render_catalog_entry.py --sha "$(git rev-parse HEAD)" --version X.Y.Z -o hermes-otel.yaml
gh release upload hermes-otel-vX.Y.Z hermes-otel.yaml
```

## Version alignment

`pyproject.toml` is the source of truth; `hermes_otel/plugin.yaml` and `.release-please-manifest.json` track it and a unit test fails if they drift. Don't edit any of them by hand; let release-please do it.
