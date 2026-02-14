resource_group = "rg-aks-demo"
cluster_name   = "aks-platform"

teams = {
  team_a = {
    vm_size    = "Standard_B2s"
    max_nodes  = 3
    rbac_group = "team-a-devs"
    quota = {
      cpu    = "4"
      memory = "8Gi"
    }
  }

  team_b = {
    vm_size    = "Standard_B2s"
    max_nodes  = 2
    rbac_group = "team-b-devs"
    quota = {
      cpu    = "2"
      memory = "4Gi"
    }
  }
}