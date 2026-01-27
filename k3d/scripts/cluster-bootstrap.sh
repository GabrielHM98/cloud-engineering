#!/usr/bin/bash
set -euo pipefail

CLUSTER_NAME=${1:-pocghm00}

echo "▶ Bootstrapping cluster: ${CLUSTER_NAME}"

# -------------------------------
# 1. Bootstrap cluster (Terraform)
# -------------------------------
echo "▶ Applying infrastructure"

cd /cloud-engineering/k3d/infra/cluster
terraform init -input=false
terraform apply -auto-approve -var="cluster_name=${CLUSTER_NAME}"

# -------------------------------
# 2. Wait for Kubernetes API
# -------------------------------
echo "▶ Waiting for Kubernetes API..."

until kubectl get nodes >/dev/null 2>&1; do
  sleep 2
done

kubectl wait --for=condition=Ready nodes --all --timeout=180s

# -------------------------------
# 3. Deploy platform components
# -------------------------------
echo "▶ Deploying platform components"

cd /cloud-engineering/k3d/infra/platform
terraform init -input=false
terraform apply -auto-approve 
# -------------------------------
# 4. Install Argo CD
# -------------------------------
echo "▶ Installing Argo CD"

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

kubectl rollout status deployment/argocd-server -n argocd

# -------------------------------
# 5. Bootstrap GitOps (in construction)
# -------------------------------
#echo "▶ Bootstrapping GitOps apps"

#kubectl apply -f gitops/argocd/root-app.yaml

echo "✅ Platform bootstrap complete"
