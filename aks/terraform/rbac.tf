resource "kubernetes_namespace_v1" "platform" {
  for_each = toset([
    "argocd",
    "monitoring",
    "ingress-nginx",
    "cert-manager",
    "velero"
  ])

  metadata {
    name = each.key
  }
} 

resource "kubernetes_namespace_v1" "apps" {
  metadata {
    name = "apps"
  }
}

# ROLEBINDINGS

resource "kubernetes_cluster_role_binding_v1" "cluster_admins" {
  metadata {
    name = "aks-cluster-admins"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "cluster-admin"
  }

  subject {
    kind      = "Group"
    name      = "aks-cluster-admins"
    api_group = "rbac.authorization.k8s.io"
  }
}

resource "kubernetes_role_binding_v1" "platform_admins" {
  for_each = kubernetes_namespace_v1.platform

  metadata {
    name      = "platform-admins"
    namespace = each.key
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "admin"
  }

  subject {
    kind      = "Group"
    name      = "aks-platform-engineers"
    api_group = "rbac.authorization.k8s.io"
  }
}

resource "kubernetes_role_binding_v1" "developers_apps" {
  metadata {
    name      = "developers-edit"
    namespace = kubernetes_namespace_v1.apps.metadata[0].name
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "edit"
  }

  subject {
    kind      = "Group"
    name      = "aks-developers"
    api_group = "rbac.authorization.k8s.io"
  }
}

#ROLES

resource "kubernetes_cluster_role_v1" "platform_engineer" {
  metadata {
    name = "platform-engineer"
  }

  rule {
    api_groups = [""]
    resources  = ["namespaces", "nodes", "services", "configmaps"]
    verbs      = ["get", "list", "watch", "create", "update", "delete"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "daemonsets", "statefulsets"]
    verbs      = ["*"]
  }

  rule {
    api_groups = ["apiextensions.k8s.io"]
    resources  = ["customresourcedefinitions"]
    verbs      = ["*"]
  }
}
