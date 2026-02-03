targetScope = 'subscription'

@description('Azure region for both the resource group and Speech resource.')
param location string = 'eastus'

@description('Name of the resource group to create or update.')
param resourceGroupName string = 'rg-rtt-speech'

@description('Globally unique name for the Speech account (letters/numbers only).')
param speechAccountName string

@description('Speech SKU tier.')
@allowed([
  'F0'
  'S0'
  'S1'
  'S2'
  'S3'
])
param speechSku string = 'S0'

@description('Optional tags applied to both the resource group and Speech resource.')
param tags object = {
  environment: 'dev'
}

resource speechRg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module speechAccount 'modules/speechAccount.bicep' = {
  name: 'speechAccountDeployment'
  scope: speechRg
  params: {
    location: location
    speechAccountName: speechAccountName
    speechSku: speechSku
    tags: tags
  }
}

output speechResourceId string = speechAccount.outputs.speechResourceId
output speechEndpoint string = speechAccount.outputs.speechEndpoint
