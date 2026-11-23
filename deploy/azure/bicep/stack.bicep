// The resources of the demo, deployed into one resource group by main.bicep.

param location string
param namePrefix string
param image string
param registryServer string
param pullIdentityId string
param identityPrincipalId string
param identityClientId string

@description('OpenTelemetry Collector image, as repository:tag in the registry.')
param collectorImage string
param environment object
@secure()
param secretEnvironment object

@description('Reverse proxy image, as repository:tag in the registry (imported from docker.io/library/caddy).')
param proxyImage string

// One public HTTPS address serves both Hermes listeners. The proxy runs in
// the same replica, so it reaches them on localhost.
var proxyPort = 8080
var apiPort = 8642
var dashboardPort = 9119
var caddyfile = '''
:8080 {
	@api path /v1/* /health /health/*
	reverse_proxy @api 127.0.0.1:8642 {
		flush_interval -1
	}
	reverse_proxy 127.0.0.1:9119
}
'''

var suffix = uniqueString(resourceGroup().id)

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs-${suffix}'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-insights-${suffix}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    // The managed OpenTelemetry agent authenticates with the connection
    // string, so local authentication has to stay on.
    DisableLocalAuth: false
  }
}

// The environment's managed OpenTelemetry agent: every container in the
// environment gets OTEL_EXPORTER_OTLP_ENDPOINT (gRPC) pointing at it, and it
// forwards traces and logs to Application Insights. It does not forward
// metrics there; those take the Collector route.
resource containerEnvironment 'Microsoft.App/managedEnvironments@2026-07-01' = {
  name: '${namePrefix}-env-${suffix}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    appInsightsConfiguration: {
      connectionString: insights.properties.ConnectionString
    }
    openTelemetryConfiguration: {
      tracesConfiguration: {
        destinations: [
          'appInsights'
        ]
      }
      logsConfiguration: {
        destinations: [
          'appInsights'
        ]
      }
    }
  }
}

// ── Metrics ──────────────────────────────────────────────────────────────
// The managed OpenTelemetry agent does not forward metrics to Application
// Insights. Metrics take Azure Monitor's native OTLP ingestion instead: the
// plugin sends them to a Collector in the same replica, which signs in with
// the app's managed identity and posts to a data collection rule that stores
// them in an Azure Monitor workspace (Prometheus-compatible).
resource metricsWorkspace 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: '${namePrefix}-metrics-${suffix}'
  location: location
}

resource collectionEndpoint 'Microsoft.Insights/dataCollectionEndpoints@2024-03-11' = {
  name: '${namePrefix}-otlp-${suffix}'
  location: location
  properties: {
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

// Shape from Microsoft's OTLP template (AzureMonitorCommunity,
// OTLP_DCE_DCR_ARM_Template), metrics part only.
resource collectionRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: '${namePrefix}-otlp-${suffix}'
  location: location
  properties: {
    dataCollectionEndpointId: collectionEndpoint.id
    references: {
      applicationInsights: [
        {
          resourceId: insights.id
          name: 'applicationInsightsResource'
        }
      ]
    }
    directDataSources: {
      otelMetrics: [
        {
          streams: [
            'Custom-Metrics-Otel'
          ]
          enrichWithResourceAttributes: [
            '*'
          ]
          enrichWithReference: 'applicationInsightsResource'
          name: 'otelMetricsDirect'
        }
      ]
    }
    destinations: {
      monitoringAccounts: [
        {
          accountResourceId: metricsWorkspace.id
          name: 'metricsWorkspace'
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Custom-Metrics-Otel'
        ]
        destinations: [
          'metricsWorkspace'
        ]
      }
    ]
  }
}

// Monitoring Metrics Publisher: lets the app's identity write to the rule.
resource publishRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: collectionRule
  name: guid(collectionRule.id, identityPrincipalId, 'metrics-publisher')
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '3913510d-42f4-4e42-8a64-420c390055eb'
    )
  }
}

var metricsEndpoint = '${collectionEndpoint.properties.metricsIngestion.endpoint}/datacollectionRules/${collectionRule.properties.immutableId}/streams/Custom-Metrics-Otel/otlp/v1/metrics'
var collectorConfig = '''
receivers:
  otlp:
    protocols:
      http:
        endpoint: 127.0.0.1:4318
processors:
  batch:
extensions:
  azure_auth:
    managed_identity:
      client_id: ${env:AZURE_CLIENT_ID}
    scopes:
      - https://monitor.azure.com/.default
exporters:
  otlphttp/azuremonitor:
    metrics_endpoint: ${env:AZURE_MONITOR_METRICS_ENDPOINT}
    auth:
      authenticator: azure_auth
service:
  extensions: [azure_auth]
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/azuremonitor]
'''

var secretNames = [for name in objectKeys(secretEnvironment): toLower(replace(name, '_', '-'))]

resource hermes 'Microsoft.App/containerApps@2026-07-01' = {
  name: '${namePrefix}-agent'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${pullIdentityId}': {}
    }
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: registryServer
          identity: pullIdentityId
        }
      ]
      ingress: {
        external: true
        targetPort: proxyPort
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        for (name, i) in objectKeys(secretEnvironment): {
          name: secretNames[i]
          // The value comes from the @secure() object above; the linter
          // cannot see that through the index.
          #disable-next-line use-secure-value-for-secure-inputs
          value: secretEnvironment[name]
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'hermes'
          image: image
          // The image's supervised gateway: API server, and the dashboard
          // when HERMES_DASHBOARD=1.
          args: [
            'gateway'
            'run'
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          volumeMounts: [
            {
              volumeName: 'data'
              mountPath: '/opt/data'
            }
          ]
          // First boot copies skills and the plugin into the data folder.
          probes: [
            {
              type: 'Startup'
              tcpSocket: {
                port: apiPort
              }
              periodSeconds: 10
              failureThreshold: 30
            }
          ]
          env: concat(
            [
              {
                // The address the browser opens; the dashboard builds its
                // sign-in links and cookies from it.
                name: 'HERMES_DEMO_PUBLIC_URL'
                value: 'https://${namePrefix}-agent.${containerEnvironment.properties.defaultDomain}'
              }
              {
                name: 'HERMES_DASHBOARD_PORT'
                value: string(dashboardPort)
              }
            ],
            map(items(environment), item => {
              name: item.key
              value: string(item.value)
            }),
            map(range(0, length(secretNames)), i => {
              name: objectKeys(secretEnvironment)[i]
              secretRef: secretNames[i]
            })
          )
        }
        {
          name: 'collector'
          image: '${registryServer}/${collectorImage}'
          args: [
            '--config=env:COLLECTOR_CONFIG'
          ]
          env: [
            {
              name: 'COLLECTOR_CONFIG'
              value: collectorConfig
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'AZURE_MONITOR_METRICS_ENDPOINT'
              value: metricsEndpoint
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
        {
          name: 'proxy'
          image: '${registryServer}/${proxyImage}'
          command: [
            'sh'
            '-c'
            'printf "%s" "$CADDYFILE" > /etc/caddy/Caddyfile && exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile'
          ]
          env: [
            {
              name: 'CADDYFILE'
              value: caddyfile
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      volumes: [
        {
          // Ephemeral: Hermes's home lives as long as the replica does.
          name: 'data'
          storageType: 'EmptyDir'
        }
      ]
      // One replica, always: Hermes keeps its sessions and the plugin's live
      // store on the container's disk.
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output applicationInsightsName string = insights.name
output containerAppName string = hermes.name
output url string = 'https://${hermes.properties.configuration.ingress.fqdn}'
output metricsWorkspaceName string = metricsWorkspace.name
output prometheusQueryEndpoint string = metricsWorkspace.properties.metrics.prometheusQueryEndpoint
