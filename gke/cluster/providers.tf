resource "google_container_node_pool" "platform" {
  name     = "platform-pool"
  cluster  = google_container_cluster.gke.name
  location = var.region

  node_count = 1

  node_config {
    machine_type = "e2-small"   # cheap AF
    disk_size_gb = 20

    preemptible = true  # spot/preemptible = much cheaper

    labels = {
      role = "platform"
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }

  autoscaling {
    min_node_count = 0
    max_node_count = 2
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}