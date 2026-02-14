variable "region" {
  type    = string
  default = "us-east-1"
}

variable "cluster_name" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "kubeconfig_path" {
  type    = string
  default = "~/.kube/config"
}

variable "teams" {
  description = "Dev teams to onboard"
  type = map(object({
    instance_types = list(string)
    max_nodes      = number
    rbac_group     = string

    quota = object({
      cpu    = string
      memory = string
    })
  }))
}