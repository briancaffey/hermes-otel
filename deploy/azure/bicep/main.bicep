// Hermes Agent on Azure Container Apps, with hermes-otel sending traces and
// logs to Application Insights through the environment's managed
// OpenTelemetry agent.
//
// Deployed into one resource group, in two passes with the same file:
//   1. without `image`: creates the container registry to build into
//   2. with `image`: creates everything else and starts Hermes
//
//   az deployment group create -g <group> -f main.bicep
//   az deployment group create -g <group> -f main.bicep -p image=<image> ...

@description('Azure region for every resource.')
param location string = resourceGroup().location

@description('Short prefix for resource names.')
@minLength(3)
@maxLength(12)
param namePrefix string = 'hermesotel'

@description('Image to run, as repository:tag in this deployment\'s registry (deploy/azure/image). Empty: create the registry only.')
param image string = ''

@description('Reverse proxy image, as repository:tag in this deployment\'s registry.')
param proxyImage string = 'caddy:2'

@description('OpenTelemetry Collector image, as repository:tag in this deployment\'s registry.')
param collectorImage string = 'opentelemetry-collector-contrib:0.162.0'

@description('Environment variables for the Hermes container that are not secret.')
param environment object = {}

@description('Secret environment variables (a model provider key, for example), name to value.')
@secure()
param secretEnvironment object = {}

module registry 'registry.bicep' = {
  name: 'registry'
  params: {
    location: location
    namePrefix: namePrefix
  }
}

module stack 'stack.bicep' = if (!empty(image)) {
  name: 'stack'
  params: {
    location: location
    namePrefix: namePrefix
    image: '${registry.outputs.loginServer}/${image}'
    registryServer: registry.outputs.loginServer
    pullIdentityId: registry.outputs.pullIdentityId
    identityPrincipalId: registry.outputs.pullIdentityPrincipalId
    identityClientId: registry.outputs.pullIdentityClientId
    collectorImage: collectorImage
    proxyImage: proxyImage
    environment: environment
    secretEnvironment: secretEnvironment
  }
}

output registryName string = registry.outputs.registryName
output applicationInsightsName string = stack.?outputs.applicationInsightsName ?? ''
output containerAppName string = stack.?outputs.containerAppName ?? ''
output url string = stack.?outputs.url ?? ''
output metricsWorkspaceName string = stack.?outputs.metricsWorkspaceName ?? ''
output prometheusQueryEndpoint string = stack.?outputs.prometheusQueryEndpoint ?? ''
