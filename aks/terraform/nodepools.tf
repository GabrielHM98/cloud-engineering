resource "azurerm_kubernetes_cluster_node_pool" "infra" {
  name                  = "infra"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
  vm_size               = "Standard_B2s"
  node_count            = 1
  mode                  = "User"

  node_labels = { "node-role.kubernetes.io/infra" = "true" }
  node_taints = [ "node-role.kubernetes.io/infra=true:NoSchedule" ]
}

#resource "azurerm_kubernetes_cluster_node_pool" "gpu" {
#  name                  = "gpu"
#  kubernetes_cluster_id = azurerm_kubernetes_cluster.aks.id
#  vm_size               = "Standard_NC6s_v3" # optional, expensive
#  node_count            = 1
#  mode                  = "User"
#
#  node_labels = { "node-role.kubernetes.io/gpu" = "true" }
#  node_taints = [ "node-role.kubernetes.io/gpu=true:NoSchedule" ]
#}