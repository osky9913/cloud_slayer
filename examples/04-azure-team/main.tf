# Scenario: Small engineering team on Azure — common Microsoft/Azure shop setup.
# Dev + prod VMs, blob storage, managed Postgres.
# Shows Azure costs vs AWS/GCP alternatives.

terraform {
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 3.0" }
  }
}

provider "azurerm" {
  features {}
}

resource "azurerm_resource_group" "main" {
  name     = "team-infra-rg"
  location = "East US"
}

# ── Storage ───────────────────────────────────────────────────────────────────

resource "azurerm_storage_account" "app_storage" {
  name                     = "teamappstorage"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_account" "backups" {
  name                     = "teambackupstorage"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "GRS"
}

# ── Compute ───────────────────────────────────────────────────────────────────

# Production API server — general purpose D-series
resource "azurerm_linux_virtual_machine" "api" {
  name                = "api-server"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  size                = "Standard_D2s_v3"

  admin_username = "adminuser"

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts"
    version   = "latest"
  }

  tags = { Role = "api", Environment = "production" }
}

# Worker VM — compute-optimized F-series for CPU-heavy tasks
resource "azurerm_linux_virtual_machine" "worker" {
  name                = "worker-vm"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  size                = "Standard_F4s_v2"

  admin_username = "adminuser"

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts"
    version   = "latest"
  }

  tags = { Role = "worker", Environment = "production" }
}

# Dev/staging VM — burstable B-series, much cheaper for intermittent use
resource "azurerm_linux_virtual_machine" "dev" {
  name                = "dev-server"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  size                = "Standard_B2s"

  admin_username = "adminuser"

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts"
    version   = "latest"
  }

  tags = { Role = "dev", Environment = "staging" }
}

# ── Database ──────────────────────────────────────────────────────────────────

resource "azurerm_postgresql_flexible_server" "app_db" {
  name                   = "team-postgres"
  resource_group_name    = azurerm_resource_group.main.name
  location               = azurerm_resource_group.main.location
  version                = "15"
  administrator_login    = "psqladmin"
  administrator_password = "stored-in-key-vault"
  storage_mb             = 32768
  sku_name               = "GP_Standard_D2s_v3"
  zone                   = "1"
}
