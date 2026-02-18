locals {
  nodegroups = {
    platform = {
      name           = "platform"
      instance_types = ["t3.small", "t3a.small"]
      capacity_type  = "SPOT"

      min_size     = 1
      max_size     = 1
      desired_size = 1

      labels = {
        role = "platform"
      }

      taints = {
        platform = {
          key    = "role"
          value  = "platform"
          effect = "NO_SCHEDULE"
        }
      }
    }
  }
}