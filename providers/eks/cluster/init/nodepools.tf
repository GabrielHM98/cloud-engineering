locals {
  nodegroups = {
    platform = {
      name           = "platform"
      instance_types = ["t3.small", "t3a.small"]
      capacity_type  = "SPOT"

      min_size     = 4
      max_size     = 4
      desired_size = 4

      labels = {
        role = "platform"
      }
    }
  }
}