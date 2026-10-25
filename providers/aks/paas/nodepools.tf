data "azurerm_kubernetes_cluster" "this" {
  name                = var.cluster_name
  resource_group_name = var.resource_group
}

resource "azurerm_kubernetes_cluster_node_pool" "teams" {
  for_each = var.teams

  name                  = substr(replace(each.key, "-", ""), 0, 12)
  kubernetes_cluster_id = data.azurerm_kubernetes_cluster.this.id

  vm_size    = each.value.vm_size
  node_count = 0

  enable_auto_scaling = true
  min_count           = 0
  max_count           = each.value.max_nodes

  mode = "User"

  node_labels = {
    team = each.key
    role = "apps"
  }

  node_taints = [
    "dedicated=${each.key}:NoSchedule"
  ]
}