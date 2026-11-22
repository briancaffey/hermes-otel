# Hermes Agent on Azure, observed with hermes-otel

A deployable demo: open-source Hermes Agent in a container on Azure Container
Apps, with hermes-otel sending its telemetry to Azure Monitor / Application
Insights. This is self-hosted Hermes Agent in your own subscription, not
Hermes Enterprise.

Status: the Bicep compiles and passes `what-if`; nothing has been deployed
with it yet. Tracking issue: #688.

## What it creates

| Resource | Purpose |
|---|---|
| Resource group `hermes-otel-demo` | Holds everything; delete it to remove the demo |
| Log Analytics workspace | Storage behind Application Insights and the container logs |
| Application Insights | Where traces and logs land |
| Container Apps environment | Runs the managed OpenTelemetry agent, which forwards traces and logs (not metrics) to Application Insights |
| Container app `hermesotel-agent` | Hermes Agent with the hermes-otel plugin |

## Preview, deploy, remove

```bash
az login
az deployment sub what-if -l eastus2 -f bicep/main.bicep -p image=<image>   # preview only
az deployment sub create  -l eastus2 -f bicep/main.bicep -p image=<image>
az group delete --name hermes-otel-demo --yes                               # removes everything
```

These resources are billed while they exist. Delete the resource group at the
end of each session.
