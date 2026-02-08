resource "azurerm_kubernetes_cluster" "aks" {
  name                = "cloudeng-poc"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  dns_prefix          = "cloudeng"

  default_node_pool {
    name            = "workers"
    node_count      = 2
    vm_size         = "Standard_DS2_v2"
    vnet_subnet_id = azurerm_subnet.aks.id
  }

  identity { type = "SystemAssigned" }

  azure_active_directory_role_based_access_control {
    managed = true
  }

  network_profile {
    network_plugin    = "azure"
    load_balancer_sku = "standard"
  }
}