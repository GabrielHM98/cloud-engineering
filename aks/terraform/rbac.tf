resource "kubernetes_namespace" "platform" {
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

resource "kubernetes_namespace" "apps" {
  metadata {
    name = "apps"
  }
}

resource "kubernetes_cluster_role_binding" "cluster_admins" {
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

resource "kubernetes_cluster_role_binding" "auditors" {
  metadata {
    name = "aks-auditors-view"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "view"
  }

  subject {
    kind      = "Group"
    name      = "aks-auditors"
    api_group = "rbac.authorization.k8s.io"
  }
}

resource "kubernetes_role_binding" "platform_admins" {
  for_each = kubernetes_namespace.platform

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

resource "kubernetes_role_binding" "developers_apps" {
  metadata {
    name      = "developers-edit"
    namespace = kubernetes_namespace.apps.metadata[0].name
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