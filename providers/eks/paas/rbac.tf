resource "kubernetes_role_binding" "team_edit" {
  for_each = var.teams

  metadata {
    name      = "${each.key}-edit"
    namespace = kubernetes_namespace.teams[each.key].metadata[0].name
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "edit"
  }

  subject {
    kind      = "Group"
    name      = each.value.rbac_group
    api_group = "rbac.authorization.k8s.io"
  }
}