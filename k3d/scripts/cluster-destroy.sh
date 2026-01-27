#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME=${1:-pocghm00}

echo "🧨 Destroying cluster: ${CLUSTER_NAME}"

# -------------------------------
# 1. Delete GitOps applications
# -------------------------------
#echo "▶ Removing GitOps applications (if present)"
#kubectl delete -f gitops/argocd/root-app.yaml --ignore-not-found || true

# -------------------------------
# 2. Uninstall Argo CD
# -------------------------------
echo "▶ Removing Argo CD"

kubectl delete namespace argocd --ignore-not-found || true

# -------------------------------
# 3. Destroy infrastructure (Terraform)
# -------------------------------
echo "▶ Destroying Terraform-managed infrastructure"

cd /cloud-engineering/k3d/infra/platform
terraform destroy -auto-approve

cd /cloud-engineering/k3d/infra/cluster
terraform destroy -auto-approve -var="cluster_name=${CLUSTER_NAME}" || true

# -------------------------------
# 4. Hard cleanup (defensive)
# -------------------------------
echo "▶ Cleaning up k3d / Docker leftovers (if any)"

k3d cluster delete "${CLUSTER_NAME}" || true

docker rm -f $(docker ps -aq --filter name=k3d) 2>/dev/null || true
docker network prune -f

echo "✅ Cluster '${CLUSTER_NAME}' fully destroyed"
