locals {
  nodegroups = {
    platform = {
      name           = "platform"
      instance_types = ["t3.small", "t3a.small"]
      capacity_type  = "SPOT"

      min_size     = 1
      max_size     = 2
      desired_size = 1

      labels = {
        role = "platform"
      }

      taints = {
        platform = {
          key    = "dedicated"
          value  = "platform"
          effect = "NO_SCHEDULE"
        }
      }
    }

    # team_a = { ... }
  }
}