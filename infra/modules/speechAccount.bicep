targetScope = 'resourceGroup'

@description('Azure region for the Speech resource.')
param location string

@description('Globally unique name of the Speech Services account (letters/numbers only).')
param speechAccountName string

@description('SKU tier for the Speech account.')
param speechSku string = 'S0'

@description('Tags applied to the Speech resource.')
param tags object = {}

resource speechAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: speechAccountName
  location: location
  kind: 'SpeechServices'
  sku: {
    name: speechSku
  }
  properties: {
    publicNetworkAccess: 'Enabled'
  }
  tags: tags
}

output speechResourceId string = speechAccount.id
output speechEndpoint string = speechAccount.properties.endpoint
