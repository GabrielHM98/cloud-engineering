resource "azurerm_kubernetes_cluster_node_pool" "infra" {
  name                  = "infra"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_D2as_v6"
  node_count            = 1
  mode                  = "User"

  node_labels = {
    "role"                    = "infra"
    "platform.cloudeng.io/tier" = "infra"
  }

  node_taints = [
    "platform.cloudeng.io/infra=true:NoSchedule"
  ]
}

#resource "azurerm_kubernetes_cluster_node_pool" "gpu" {
#  name                  = "gpu"
#  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
#  vm_size               = "Standard_D2as_v6" # optional, expensive
#  node_count            = 1
#  mode                  = "User"
#
#  node_labels = { "node-role.kubernetes.io/gpu" = "true" }
#  node_taints = [ "node-role.kubernetes.io/gpu=true:NoSchedule" ]
#}