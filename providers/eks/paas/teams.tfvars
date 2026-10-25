cluster_name = "ghmekspoc00"
subnet_ids   = ["subnet-aaa", "subnet-bbb"]

teams = {
  team_a = {
    instance_types = ["t3.medium"]
    max_nodes      = 3
    rbac_group     = "team-a-devs"

    quota = {
      cpu    = "4"
      memory = "8Gi"
    }
  }

  team_b = {
    instance_types = ["t3.small"]
    max_nodes      = 2
    rbac_group     = "team-b-devs"

    quota = {
      cpu    = "2"
      memory = "4Gi"
    }
  }
}