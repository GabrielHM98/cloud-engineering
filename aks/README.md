# ☁️ AKS Platform Engineering Demo (Terraform + ArgoCD + GitOps)

This repository demonstrates a **production-style Kubernetes platform setup on Azure AKS**, built with:

- **Terraform** for infrastructure provisioning  
- **Helm** for packaging platform components  
- **ArgoCD** for GitOps continuous delivery  
- **Ingress-NGINX** with multiple ingress classes  
- **Node pool isolation** (infra vs workers)  
- **RBAC** modeled declaratively  

The goal is to showcase **end-to-end platform engineering**: from cloud infra → cluster bootstrap → GitOps-managed platform capabilities → ingress segmentation.

---

## 🧱 Architecture Overview

Azure Provider
└── AKS Cluster
├── Infra Node Pool
│   ├── ArgoCD
│   ├── Infra Ingress Controller
│   └── Platform Services (Grafana, Prometheus, etc.)
└── Worker Node Pool
    ├── External Ingress Controller (public)
    ├── Internal Ingress Controller (private)
    └── Application Workloads


---

## 📁 Repository Structure
aks
├── infra/
│   ├── main.tf 
│   ├── aks.tf
│   ├── networks.tf
│   ├── nodepools.tf
│   ├── storage.tf
│   ├── azuread-identity.tf 
│   ├── providers.tf 
│   ├── variables.tf 
│   └── rbac.tf 
│
├── gitops/
│   ├── argocd-bootstrap/
│   │   ├── Chart.yaml
│   |   └── values.yaml
│   └── ingresscontroller/
│       ├── appset/
│       │   └── ingress.yaml  
│       └──  ingresscontroller-app.yaml 
│       
└── README.md


---

## 🚀 What This Demo Covers

- AKS provisioning with Terraform  
- Separate node pools for infra vs workloads  
- ArgoCD installed via Helm and pinned to infra nodes  
- GitOps model for platform components  
- Three ingress controllers with separate ingress classes  
- Internal vs external load balancers  
- Clean DNS patterns for platform services  
- Declarative RBAC model  

---

## ⚙️ Prerequisites

- Azure subscription  
- Azure CLI (`az`)  
- Terraform ≥ 1.5  
- kubectl  
- Helm ≥ 3.x  

Login:

```bash
az login
az account set --subscription <your-subscription-id>
```

## 🏗️ Provision the AKS Cluster

```bash
cd infra
terraform init
terraform apply
```

Configure kubectl:

```bash
az aks get-credentials \
  --resource-group cloudeng-poc-rg \
  --name cloudeng-poc
```

## 📦 Install ArgoCD (Platform Control Plane)

```bash
kubectl create namespace argocd
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update

helm install argocd-config argo/argo-cd \
  -n argocd-config \
  -f gitops/platform/argocd/values.yaml
```

## 🌐 Access ArgoCD

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d && echo
```

## 🚦 Ingress Controllers (GitOps)

```bash
kubectl create /cloud-engineering/aks/gitops/ingresscontroller/ingresscontroller-app.yaml
```

Verify:

```bash
kubectl get ingressclass
kubectl get svc -n ingress-external
kubectl get svc -n ingress-internal
kubectl get svc -n ingress-infra
```

## 🔐 RBAC Model (High-Level)

| Role               | Scope        | Permissions                     |
| ------------------ | ------------ | ------------------------------- |
| Cluster Admins     | Cluster-wide | Full access                     |
| Platform Engineers | Cluster-wide | Manage platform components      |
| Developers         | Namespace    | Deploy and manage app workloads |
