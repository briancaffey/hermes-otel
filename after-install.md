## This is the hermes-otel repository, not the plugin

You installed the repository root. The plugin package lives in the
`hermes_otel/` subdirectory, and that is what Hermes needs to install — the
rest of this repo is documentation, tests and example Compose stacks.

Nothing here will load: there is no `plugin.yaml` at this level, which is what
the warning above means.

Install the plugin instead:

```bash
hermes plugins remove hermes-otel
hermes plugins install hermes_otel                          # from the Hermes plugin catalog
# or the newest main from this repository:
hermes plugins install briancaffey/hermes-otel/hermes_otel
hermes plugins enable hermes_otel
```

Same destination as before — it unpacks to `~/.hermes/plugins/hermes_otel/`,
because the install location comes from `plugin.yaml`, not from the path you
typed.

Why the change: Hermes security-scans a plugin's whole file tree before
installing it, and grades docs and test fixtures like executable code. Shipping
only `hermes_otel/` keeps installs unblocked and keeps the install small (about
1.7 MB, against more than 50 MB for the whole repository). See
https://github.com/briancaffey/hermes-otel/issues/53

Docs: https://briancaffey.github.io/hermes-otel/
