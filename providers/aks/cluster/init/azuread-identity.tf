resource "azuread_group" "cluster_admins" {
  display_name     = "aks-cluster-admins"
  security_enabled = true
}

resource "azuread_group" "platform_engineers" {
  display_name     = "aks-platform-engineers"
  security_enabled = true
}

resource "azuread_group" "developers" {
  display_name     = "aks-developers"
  security_enabled = true
}