module "dev_nodegroups" {
  for_each = var.teams

  source  = "terraform-aws-modules/eks/aws//modules/eks-managed-node-group"
  version = "~> 20.0"

  cluster_name = var.cluster_name
  subnet_ids   = var.subnet_ids

  name           = each.key
  instance_types = each.value.instance_types
  capacity_type  = "SPOT"

  min_size     = 0
  max_size     = each.value.max_nodes
  desired_size = 0

  labels = {
    team = each.key
    role = "apps"
  }

  taints = {
    team = {
      key    = "dedicated"
      value  = each.key
      effect = "NO_SCHEDULE"
    }
  }
}