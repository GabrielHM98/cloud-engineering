provider "aws" {
  region = var.region
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}