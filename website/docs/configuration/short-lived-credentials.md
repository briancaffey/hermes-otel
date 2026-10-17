---
sidebar_position: 3
title: "Short-lived credentials"
description: "headers_helper: a command that mints the OTLP headers for a backend entry, re-run once and the batch retried once when the backend answers 401/403. For Google Cloud access tokens, vault-issued OIDC tokens, rotated API keys."
---

# Short-lived credentials (`headers_helper`)

The plugin's headers are static: `headers:` on the entry, the `*_env` credential
fields, the top-level `headers:` block. A token that expires cannot live there.
A Google Cloud access token is good for an hour, an OIDC token from a vault for
minutes, and a password someone rotates is wrong the moment they do; until now
the answers were a restart or an [OpenTelemetry Collector](/backends/collector)
in front of the plugin doing the auth.

`headers_helper:` on a backend entry names a command instead (#483). The plugin
runs it when the entry's exporters are built, parses its stdout as headers, and
sends them on every signal of that backend. When the backend answers **401 or
403**, the plugin runs the helper once more, puts the new headers on the
exporter, and retries **the same batch once**. A second refusal is logged and
the batch is dropped, like any other failed export; the next batch tries again
with the headers the helper printed last.

```yaml
backends:
  - type: otlp
    name: cloud-trace
    endpoint: https://telemetry.googleapis.com/v1/traces
    headers_helper: /home/me/.hermes/mint-gcp-headers.sh
    headers_helper_timeout_ms: 5000        # default
```

## The contract

| | |
|---|---|
| **Command** | An argv string, split with POSIX `shlex` rules (`"a b"` is one argument), run with **no shell**. Pipes, `$(...)` and redirections belong inside the script you name. `${VAR}` in the string is expanded when the config loads, like every other entry field. |
| **Environment** | The plugin's own: whatever Hermes's process has, so `gcloud`, `aws`, `vault` and friends find their config. The helper runs as the user running Hermes. |
| **Timeout** | `headers_helper_timeout_ms`, default 5000. A run that takes longer counts as failed. |
| **stdout** | Either one JSON object (`{"Authorization": "Bearer …", "x-goog-user-project": "p"}`) or one `Name=value` per line. Blank lines and `#` comments are skipped. Values may contain `=`. |
| **Exit code** | Non-zero is a failure: the message carries the code and the first lines of stderr (redacted). |
| **Precedence** | Minted headers sit on top of the entry's static `headers:` and of what the type's resolver built (an OpenObserve entry's `Authorization: Basic …`, Honeycomb's `x-honeycomb-team`). The helper is the live credential, so on the same name it wins. Print the full set every run: a header it stops printing is removed from the requests. |
| **Signals** | One helper process serves the entry's traces, metrics and logs exporters; each gets the same minted set and each refreshes on its own 401/403. |

A helper that fails **at startup** skips that backend entry with the error in
the startup log (`✗ cloud-trace traces init failed: headers_helper … exited 1`),
the way a missing credential does; the other entries proceed. A helper that
fails **during a refresh** leaves the old headers in place, the refused batch is
dropped, and the next batch triggers the next attempt. Nothing in this path ever
blocks the agent: exports run on the batch processors' threads.

The Settings tab's backend card shows the command and the timeout as the
entry's source for its headers, never what the helper printed. `debug.log`
(`HERMES_OTEL_DEBUG=true`) records each run with the header **names** and
redacted values: `headers_helper cloud-trace: run 1 (412 ms) ->
Authorization=••••••, x-goog-user-project=my-project`, and
`401 from the backend; re-ran the helper and retrying the batch once` when a
refresh happens. See [Privacy](/configuration/privacy) for what the redactor
masks.

## Example: Google Cloud access token

Cloud Trace, Cloud Monitoring and Cloud Logging take OTLP/HTTP at
`telemetry.googleapis.com` with an OAuth access token and the quota project in
`x-goog-user-project`. `gcloud auth print-access-token` prints a token good for
about an hour. The helper, saved outside the plugin directory so an upgrade
keeps it:

```bash
#!/bin/sh
# ~/.hermes/mint-gcp-headers.sh — chmod +x
set -eu
printf 'Authorization=Bearer %s\n' "$(gcloud auth print-access-token)"
printf 'x-goog-user-project=%s\n' "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
```

```yaml
backends:
  - type: otlp
    name: google-cloud
    endpoint: https://telemetry.googleapis.com/v1/traces   # /v1/metrics and /v1/logs are derived
    headers_helper: /home/me/.hermes/mint-gcp-headers.sh
    metrics: true
    logs: true
capture_logs: true
```

When the token expires, the next export gets a 401, the helper mints a new one
and that batch lands on the retry. This example is documented, not verified
against a Google Cloud project (the same standing as the Collector's
`googlecloud` target).

Any issuer with a command-line works the same way: `vault read -field=token …`,
`aws`-signed presigned headers from a small script, `op read` for a rotated key,
or a script that reads a file another process keeps fresh.

## Example: a rotated password (OpenObserve)

The explicit types build their auth header from the entry's fields; a helper
replaces it with a fresh value. For OpenObserve, which takes HTTP Basic auth, a
helper that reads the password from a file:

```bash
#!/bin/sh
# ~/.hermes/mint-oo-headers.sh
set -eu
auth=$(printf '%s:%s' "$OPENOBSERVE_USER" "$(cat "$HOME/.hermes/oo-password")" | base64 | tr -d '\n')
printf 'Authorization=Basic %s\n' "$auth"
```

```yaml
backends:
  - type: openobserve
    endpoint: http://localhost:5080/api/default/v1/traces
    user_env: OPENOBSERVE_USER                 # the type still wants its fields;
    password_env: OPENOBSERVE_PASSWORD         # the helper's Authorization wins
    headers_helper: /home/me/.hermes/mint-oo-headers.sh
capture_logs: true
```

Change the user's password in the OpenObserve UI, write the new one to
`~/.hermes/oo-password`, and the next batch goes: a 401, one helper run, one
retry that lands. `debug.log` shows the sequence; in OpenObserve the stream keeps
filling with no gap beyond that one batch's retry.

## When to use the Collector instead

The helper mints headers; it cannot sign a request. Backends that want a
signature over the body (AWS SigV4 for X-Ray and CloudWatch) still need the
[Collector](/backends/collector) and its exporter. The Collector is also the
place for processing the plugin does not do (temporality conversion for Azure
Monitor, sampling, extra redaction). For a plain bearer token or API key that
expires, the helper is the smaller setup: no second process, the plugin's
`debug.log` still records the real backend's answer per batch, and the dashboard
keeps querying the backend directly.

## Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `headers_helper` | string | *(unset)* | The command, as an argv string (no shell). Its stdout, a JSON object or `Name=value` lines, is merged over the entry's static headers on every signal; re-run once and the batch retried once on a 401/403 |
| `headers_helper_timeout_ms` | int | `5000` | How long one run may take |

Both are per backend entry; there is no top-level form. The fields are listed
with the other shared entry fields in the [config schema](/reference/config-schema#backends-entries).
