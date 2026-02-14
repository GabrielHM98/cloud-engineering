module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.cluster_name}-vpc"
  cidr = "10.0.0.0/16"

  azs            = ["${var.region}a", "${var.region}b"]
  public_subnets = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway     = false
  single_nat_gateway     = false
  enable_dns_hostnames  = true

  map_public_ip_on_launch = true

  tags = {
    Name = "${var.cluster_name}-vpc"
  }
}