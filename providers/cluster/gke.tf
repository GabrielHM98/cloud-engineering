resource "google_container_cluster" "gke" {
  name     = var.cluster_name
  location = var.region

  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode = "VPC_NATIVE"

  release_channel {
    channel = "REGULAR"
  }

  deletion_protection = false
}