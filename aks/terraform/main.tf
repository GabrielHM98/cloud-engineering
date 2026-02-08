resource "azurerm_resource_group" "rg" {
  name     = "cloudeng-poc-rg"
  location = var.location
}