locals {
  nodegroups = {
    platform = {
      name           = "platform"
      instance_types = ["t3.small", "t3a.small"]
      capacity_type  = "SPOT"

      min_size     = 3
      max_size     = 3
      desired_size = 3

      labels = {
        role = "platform"
      }
    }
  }
}