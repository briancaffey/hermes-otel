// The container registry the demo image is built into, and the identity the
// container app pulls with. Deployed before the image exists.

param location string
param namePrefix string

var suffix = uniqueString(resourceGroup().id)

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: take('${namePrefix}acr${suffix}', 50)
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-pull'
  location: location
}

// AcrPull: the container app reads images with its identity, no password.
resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, pullIdentity.id, 'acrpull')
  properties: {
    principalId: pullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

output registryName string = registry.name
output loginServer string = registry.properties.loginServer
output pullIdentityId string = pullIdentity.id
output pullIdentityPrincipalId string = pullIdentity.properties.principalId
output pullIdentityClientId string = pullIdentity.properties.clientId
