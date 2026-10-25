variable "resource_group" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "kubeconfig_path" {
  type    = string
  default = "~/.kube/config"
}

variable "teams" {
  description = "Dev teams to onboard"
  type = map(object({
    vm_size     = string
    max_nodes   = number
    rbac_group  = string
    quota = object({
      cpu    = string
      memory = string
    })
  }))
}