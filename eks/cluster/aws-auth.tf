module "eks_aws_auth" {
  source  = "terraform-aws-modules/eks/aws//modules/aws-auth"
  version = "~> 20.0"

  cluster_name = module.eks.cluster_name

  aws_auth_users = [
    {
      userarn  = "arn:aws:iam::438987839708:user/admin"
      username = "admin"
      groups   = ["system:masters"]
    }
  ]
}