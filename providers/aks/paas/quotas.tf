resource "kubernetes_resource_quota" "teams" {
  for_each = var.teams

  metadata {
    name      = "quota"
    namespace = kubernetes_namespace.teams[each.key].metadata[0].name
  }

  spec {
    hard = {
      "cpu"    = each.value.quota.cpu
      "memory" = each.value.quota.memory
    }
  }
}