# GPU-Enabled Kubernetes Platform PoC (k3d + k3s + k8s)

This repository contains a **local, reproducible Kubernetes platform proof-of-concept** showcasing:

* multi-node cluster topology
* infra / app / GPU workload separation
* Kubernetes scheduling via labels, taints, and tolerations
* NVIDIA GPU workloads running on Kubernetes
* Infrastructure bootstrapped with Terraform
* A design that mirrors **real production patterns** (EKS / GKE / OpenShift) while remaining lightweight and low-cost

This project is intentionally **local-first**: it demonstrates *platform engineering concepts*, not cloud billing.

---

## 🧠 What This Demonstrates

### Platform & Infrastructure Skills

* Kubernetes cluster bootstrapping
* Node role separation (infra vs application vs GPU)
* Taints, tolerations, and node selectors
* Resource isolation and scheduling semantics
* GPU device exposure and scheduling model
* Terraform used at the correct abstraction boundary

### ML / GPU Awareness

* NVIDIA GPU passthrough into Kubernetes
* Device plugin model (`nvidia.com/gpu`)
* Explicit GPU workload scheduling
* Honest local approximation of managed GPU clusters

### Engineering Judgment

* Correct handling of tools without stable APIs (`null_resource`)
* Clear separation between:

  * container runtime concerns (Docker)
  * node behavior (k3s)
  * scheduling semantics (Kubernetes)
* Explicit documentation of tradeoffs

---

## 🏗️ Architecture Overview

### Node Topology

| Node     | Role          | Purpose                         |
| -------- | ------------- | ------------------------------- |
| server-0 | control-plane | Kubernetes API & control plane  |
| agent-0  | app           | CPU-based application workloads |
| agent-1  | infra         | ingress, Argo CD, observability |
| agent-2  | gpu           | GPU-accelerated workloads       |

### Scheduling Model

* **Infra node**

  * labeled and tainted (`NoSchedule`)
  * only infra workloads tolerate it
* **GPU node**

  * labeled and tainted as well
  * only GPU workloads can schedule
* **App node**

  * default scheduling target

GPU workloads must **explicitly request GPUs** and **tolerate the GPU taint**.

---

## 📁 Repository Structure

```text
.
├── infra/
│   ├──cluster
│   │   ├── k3d.tf            # Cluster bootstrap (Terraform + k3d)
│   │   ├── providers.tf      # Cluster configuration
│   │   └── variables.tf
│   ├──platform
│   │   ├── k8S.tf           # Labels and taints 
│   │   ├── providers.tf 
│   │   └── variables.tf
│   └──README.md 
├── app/
├── gitops/               # (optional) Argo CD / GitOps manifests
├── .gitignore
└── README.md
```

* **Terraform** is used only to bootstrap the cluster.
* **Applications** live under `apps/` and are deployed as Kubernetes manifests.
* **GitOps** tooling (if enabled) manages workloads declaratively after bootstrap.

---

## 🔧 Prerequisites

### Host System

* Linux (native or WSL2)
* NVIDIA GPU (tested with RTX series)
* At least **8 GB RAM** recommended

### Required Tools

| Tool                     | Purpose                  |
| ------------------------ | ------------------------ |
| Docker                   | Container runtime        |
| NVIDIA drivers           | GPU support              |
| NVIDIA Container Toolkit | GPU passthrough          |
| k3d                      | Local Kubernetes         |
| kubectl                  | Cluster interaction      |
| Terraform                | Infrastructure bootstrap |

### GPU Prerequisites (Mandatory)

Verify GPU on host:

```bash
nvidia-smi
```

Verify Docker GPU support (Image can change depending on OS):

```bash
docker run --rm --gpus all nvidia/cuda:13.1.1-cudnn-devel-ubuntu24.04 nvidia-smi
```

If either command fails, GPU workloads will not function correctly.

---

## 🚀 Cluster Creation

From the `infra/` directory:

```bash
terraform init
terraform apply
```

Terraform will:

* create a k3d cluster
* define node roles at bootstrap
* expose GPU(s) to the cluster
* apply labels and taints correctly

> **Note**: k3d exposes GPUs cluster-wide. GPU *usage* is restricted at the Kubernetes scheduling layer.

---

## 🎮 Enable GPU Scheduling in Kubernetes

Install the NVIDIA device plugin:

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.15.0/nvidia-device-plugin.yml
```

Verify:

```bash
kubectl describe node | grep nvidia.com/gpu
```

---

## 🧪 GPU Smoke Test

Example GPU test pod:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: gpu-test
spec:
  nodeSelector:
    node-role.kubernetes.io/gpu: "true"
  tolerations:
  - key: node-role.kubernetes.io/gpu
    operator: Equal
    value: "true"
    effect: NoSchedule
  containers:
  - name: cuda
    image: nvidia/cuda:13.1.1-cudnn-devel-ubuntu24.04
    command: ["nvidia-smi"]
    resources:
      limits:
        nvidia.com/gpu: 1
  restartPolicy: Never
```

Apply and inspect logs:

```bash
kubectl apply -f gpu-test.yaml
kubectl logs gpu-test
```
---

## 🧩 Downscaler Operator

This repository includes a custom Kubernetes operator responsible for:
- downscaling deployments with low request rates
- reacting to observability signals
- conserving resources on constrained clusters

The operator is developed independently and deployed via GitOps.

---

## ⚠️ Design Notes & Tradeoffs

* This is **not** a managed cloud cluster
* GPU visibility is global; isolation is enforced via scheduling
* Node identity is defined **at bootstrap**, not mutated later
* Terraform is used where declarative APIs exist; CLIs are treated as imperative boundaries
* The setup mirrors real-world patterns without pretending to be production

These choices are **intentional** and documented.

---

## 🌍 Why This Matters

This PoC demonstrates:

* platform engineering fundamentals
* Kubernetes-native thinking
* ML/GPU workload awareness
* correct abstraction boundaries
* cost-conscious experimentation

It is designed to be **read, reasoned about, and extended**, not just executed.

---

## 🔮 Future Extensions

* Argo CD deployment (infra node only)
* GPU inference service (TensorFlow / PyTorch / Triton)
* Prometheus + Grafana with GPU metrics (Also designed to work on infra nodes)
* Autoscaling or custom operators
* Migration of topology to EKS / GKE

---

## 📬 About

Built as a learning and demonstration project by a cloud / platform engineer with interests in:

* Kubernetes internals
* GPU workloads
* distributed systems
* ML infrastructure
