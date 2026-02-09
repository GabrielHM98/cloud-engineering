# ☁️ Cloud Engineering Portfolio

This repository is a **hands-on cloud engineering portfolio** showcasing real-world infrastructure, platform engineering, and Kubernetes patterns across multiple cloud providers.

The goal is to demonstrate:
- Infrastructure as Code (Terraform)
- Kubernetes platform design (AKS, EKS, GKE)
- GitOps workflows (ArgoCD, Helm)
- Custom Kubernetes operators
- Cloud-native observability and ingress architectures
- GPU-enabled workloads for AI/ML applications

This repo evolves over time as new experiments, PoCs, and platform components are added.

---

## 🧠 What You’ll Find Here

### 🏗️ Infrastructure as Code
- Terraform modules and demos for:
  - Azure (AKS)
  - AWS (EKS)
  - GCP (GKE)
- Networking, node pools, RBAC, identity, storage, and security patterns

### ⚙️ Platform Engineering
- GitOps-based platform bootstrapping using ArgoCD
- Multi-ingress architectures (external, internal, infra)
- Node pool isolation for platform vs workloads
- Observability stack (Prometheus, Grafana, Thanos – in progress)
- Certificate management and ingress TLS (planned)

### 🧩 Kubernetes Operators
- Custom operators for:
  - Autoscaling / downscaling workloads
  - Platform automation
  - Policy enforcement (OPA/Gatekeeper experiments)
- Operator development experiments (controller-runtime / Kubebuilder)

### 🤖 AI / GPU Workloads (in progress)
- GPU-enabled Kubernetes node pools
- AI demo applications using:
  - TensorFlow / Keras
  - Transformers
- End-to-end example: model inference service deployed on Kubernetes with GPU support

---

## 📁 Repository Structure

```text
├── aks/ # Azure AKS demos and platform setup
├── eks/ # AWS EKS demos (planned)
├── gke/ # GCP GKE demos (planned)
├── operators/ # Custom Kubernetes operators
├── apps/
│   └── ai-gpu-app/ # GPU-enabled AI application (WIP)
└── README.md
```

Each provider folder is self-contained and includes:
- Terraform infrastructure
- GitOps bootstrap
- Platform components (ingress, observability, etc.)

---

## 🎯 Goals of This Repository

- Build a **realistic platform engineering portfolio**
- Explore **multi-cloud Kubernetes architectures**
- Practice **end-to-end automation** (infra → cluster → platform → apps)
- Experiment with **GPU workloads on Kubernetes**
- Develop **custom operators** to automate platform concerns

---

## 🚀 Getting Started

Each subproject contains its own README with setup instructions.

Example:

```bash
cd aks/infra
terraform init
terraform apply
```

## 🧭 Roadmap (Living Document)

- [x] AKS platform PoC 
- [ ] EKS platform PoC
- [ ] GKE platform PoC
- [ ] Observability stack (Prometheus + Grafana + Thanos)
- [ ] cert-manager + TLS automation
- [ ] GPU node pool + AI inference service
- [ ] Custom autoscaling / lifecycle operator

## 👋 About

This repository is part of a continuous learning journey in cloud and platform engineering.
It focuses on building realistic, production-inspired architectures.