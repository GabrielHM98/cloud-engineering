# AGENT-0: INFRA NODE

resource "kubernetes_labels" "infra_node" {
  
  api_version = "v1"
  kind        = "Node"

  metadata {
    name = "k3d-${var.cluster_name}-agent-0"
  }

  labels = {
    "node-role.kubernetes.io/infra" = "true"
  }

  field_manager = "Sheneska"
}

resource "kubernetes_node_taint" "infra_node" {

  depends_on = [kubernetes_labels.infra_node]
  
  metadata {
    name = "k3d-${var.cluster_name}-agent-0"
  }

  taint {
    key    = "node-role.kubernetes.io/infra"
    value  = "true"
    effect = "NoSchedule"
  }
}


#AGENT-1: GENERIC WORKER NODE

resource "kubernetes_labels" "worker_node" {

  api_version = "v1"
  kind        = "Node"

  metadata {
    name = "k3d-${var.cluster_name}-agent-1"
  }

  labels = {
    "node-role.kubernetes.io/worker" = "true"
  }
}

# AGENT-2: GPU WORKER NODE

resource "kubernetes_labels" "gpu_node" {

  api_version = "v1"
  kind        = "Node"

  metadata {
    name = "k3d-${var.cluster_name}-agent-2"
  }

  labels = {
    "node-role.kubernetes.io/gpu" = "true"
  }
}

resource "kubernetes_node_taint" "gpu_node" {

  depends_on = [kubernetes_labels.gpu_node]

  metadata {
    name = "k3d-${var.cluster_name}-agent-2"
  }

  taint {
    key    = "node-role.kubernetes.io/gpu"
    value  = "true"
    effect = "NoSchedule"
  }

  field_manager = "Sheneska"

}
