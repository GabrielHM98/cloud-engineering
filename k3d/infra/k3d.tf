resource "null_resource" "k3d_cluster" {
  triggers = {
    cluster_name = var.cluster_name
  }

  provisioner "local-exec" {
    command = <<EOF
set -e

k3d cluster create ${self.triggers.cluster_name} \
  --servers 1 \
  --servers-memory 512m \
  --agents 3 \
  --agents-memory 256m \
  --k3s-node-label node-role.kubernetes.io/app=true@agent:0 \
  --k3s-node-label node-role.kubernetes.io/infra=true@agent:1 \
  --k3s-arg "--node-taint=node-role.kubernetes.io/infra=true:NoSchedule@agent:1" \
  --k3s-node-label node-role.kubernetes.io/gpu=true@agent:2 \
  --k3s-arg "--node-taint=node-role.kubernetes.io/gpu=true:NoSchedule@agent:2" \
  --gpus=all
  --verbose
EOF
  }

  provisioner "local-exec" {
    when    = destroy
    command = "k3d cluster delete ${self.triggers.cluster_name}"
  }
}