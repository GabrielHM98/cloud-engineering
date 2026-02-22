module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = "1.32"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.public_subnets

  # Public endpoint so you can kubectl from your laptop
  cluster_endpoint_public_access = true

  eks_managed_node_groups = {
    spot_small = {
      name           = "spot-small"
      instance_types = ["t3.small", "t3a.small"]
      capacity_type  = "SPOT"

      min_size     = 1
      max_size     = 1
      desired_size = 1

      labels = {
        lifecycle = "spot"
        role      = "infra"
      }
    }
  }

  enable_cluster_creator_admin_permissions = true

  tags = {
    Environment = "demo"
    ManagedBy   = "terraform"
  }
}