resource "azurerm_storage_account" "platform" {
  name                     = "cloudengpocsa"
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_container" "velero" {
  name                  = "velero-backups"
  storage_account_name  = azurerm_storage_account.platform.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "thanos" {
  name                  = "thanos-metrics"
  storage_account_name  = azurerm_storage_account.platform.name
  container_access_type = "private"
}