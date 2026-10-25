resource "kubernetes_namespace" "teams" {
  for_each = var.teams

  metadata {
    name = each.key
    labels = {
      team = each.key
    }
  }
}