terraform {
  required_version = ">= 1.5"
}

provider "null" {}

provider "kubernetes" {
  config_path = "~/.kube/config"
}