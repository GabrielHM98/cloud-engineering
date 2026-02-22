module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = "1.35"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.public_subnets

  cluster_endpoint_public_access = true
  cluster_encryption_config   = {} 

  eks_managed_node_groups = {
    spot_small = {
      name           = "spot-small"
      instance_types = ["t3.small", "t3a.small"]
      capacity_type  = "SPOT"

      min_size     = 4
      max_size     = 4
      desired_size = 4

      labels = {
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