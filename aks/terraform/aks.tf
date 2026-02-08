resource "azurerm_kubernetes_cluster" "aks" {
  name                = "cloudeng-poc"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  dns_prefix          = "cloudeng"

  default_node_pool {
    name            = "workers"
    node_count      = 2
    vm_size         = "Standard_D2as_v6"
    vnet_subnet_id = azurerm_subnet.aks.id
    node_labels = { "role" = "worker" }
  }

  identity { type = "SystemAssigned" }

  network_profile {
    network_plugin = "azure"
    service_cidr       = "10.2.0.0/16"
    dns_service_ip     = "10.2.0.10"
  }
}