variable "project_id" {
  description = "GCP project id"
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"  # cheapest + lots of capacity
}

variable "cluster_name" {
  type    = string
  default = "gke-demo-cheap"
}