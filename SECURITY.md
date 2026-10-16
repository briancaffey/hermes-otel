# Security policy

hermes-otel runs inside the Hermes Agent process and ships every prompt,
response and tool call to the OTLP backends you configure. Anything that
could leak that data, or a credential, to the wrong place is in scope.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private vulnerability reporting for this repository:
**Security → Report a vulnerability** at
<https://github.com/briancaffey/hermes-otel/security/advisories/new>.
You will get an acknowledgement within a week; fixes ship as a normal
release with a changelog entry once the report is resolved, and you are
credited unless you ask not to be.

## What is in scope

- The plugin (`hermes_otel/`): the hooks, the export pipeline, the
  backend resolvers, header and credential handling, redaction.
- The dashboard (`hermes_otel/dashboard/`, `dashboard-ui/`) and the query
  CLI, which read traces, prompts and credentials from the config file.
- The MCP query server configuration the plugin writes.

Out of scope: the backends themselves (report those upstream), Hermes
Agent (report at <https://github.com/nousresearch/hermes-agent>), and the
docker-compose stacks, which are development conveniences with default
credentials by design.

## How credentials are handled today

- Backend credentials come from the config file or the environment
  variables a backend entry names (`*_env` keys); the settings report and
  the dashboard mask them, and log redaction strips credential shapes
  from exported log records.
- The OTLP exporters are built with only the headers the config names.
  The SDK's `OTEL_EXPORTER_OTLP_*HEADERS` variables are ignored, so a
  token exported for another tool never reaches a backend that was not
  given it (#472).
- Every release is checked by the Hermes plugin security scanner in CI
  (`scripts/scan_plugin_artifact.py`), which must report `safe` for the
  install artifact and the repository.

## Supported versions

Only the latest release receives security fixes.
