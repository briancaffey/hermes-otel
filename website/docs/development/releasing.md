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
| `fix:` | Patch (0.1.0 → 0.1.1) |
| `feat:` | Minor (0.1.0 → 0.2.0) |
| `feat!:` or `BREAKING CHANGE:` in body | Major (0.1.0 → 1.0.0) |
| `chore:`, `refactor:`, `docs:`, `test:` | No bump |

So `feat(backends): add honeycomb` → minor bump + changelog entry under "Features". `fix(hooks): avoid double-ending span` → patch bump + entry under "Bug Fixes".

## The Release PR

After a qualifying commit lands, release-please opens a PR titled something like:

```text
chore(main): release hermes-otel 0.2.0
```

The PR updates:

- `pyproject.toml` version field
- `CHANGELOG.md` with the commit subjects grouped by type
- `.release-please-manifest.json` (internal tracking file)

Review the changelog entries — if you want to tweak wording, edit the PR directly and re-merge.

## Configuration

Two files control release-please:

- `release-please-config.json` — bump rules, component structure, tag format
- `.release-please-manifest.json` — current version

```json
// release-please-config.json (current)
{
  "packages": {
    ".": {
      "release-type": "python",
      "package-name": "hermes-otel",
      "bump-minor-pre-major": true,
      "include-component-in-tag": false
    }
  }
}
```

`bump-minor-pre-major: true` means `feat:` commits bump the minor (not major) until 1.0 — standard for pre-1.0 projects.

`include-component-in-tag: false` means tags are `vX.Y.Z`, not `hermes-otel-vX.Y.Z` (a legacy default from multi-package repos).

## The workflow

`.github/workflows/release-please.yml` runs on every push to `main`:

```yaml
on:
  push:
    branches: [main]

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@v4
        with:
          token: ${{ secrets.GH_PAT }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

### Why a PAT instead of `GITHUB_TOKEN`?

The `GH_PAT` secret is a personal access token with `contents: write` + `pull-requests: write`. It's used instead of `GITHUB_TOKEN` because:

- A Release PR is opened by `GITHUB_TOKEN` → GitHub doesn't trigger downstream workflows (CI) on that push (loop protection).
- With a PAT, the push *does* trigger CI — so the Release PR is checked before merge.

## Publishing to PyPI (future)

Not yet wired up. When it is:

1. Add a `release` trigger on tags to `.github/workflows/publish.yml`.
2. Build wheel + sdist with `uv build`.
3. Publish with `uv publish` or `twine` using a PyPI token stored as a secret.
4. Include trusted-publisher OIDC if PyPI accepts it for the org.

Open an issue if you'd like to see this sooner.

## Manual release

You should almost never need this — release-please handles everything. But if the action is broken:

```bash
# Bump version
vim pyproject.toml

# Update changelog
vim CHANGELOG.md

# Tag and push
git commit -am "chore(main): release hermes-otel 0.X.Y"
git tag v0.X.Y
git push origin main --tags

# Create GitHub Release
gh release create v0.X.Y --notes-from-tag
```

## Version alignment

`pyproject.toml` is the source of truth. `.release-please-manifest.json` tracks it. Don't edit either by hand — let release-please do it.
