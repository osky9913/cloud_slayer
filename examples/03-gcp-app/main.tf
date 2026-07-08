# Scenario: GCP-native web application — typical Google Cloud setup.
# Frontend/API on GCE, Cloud Storage for assets, Cloud SQL for data.
# Shows GCP costs vs AWS/Azure alternatives.

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

provider "google" {
  project = "my-gcp-project"
  region  = "us-east1"
}

# ── Storage ───────────────────────────────────────────────────────────────────

resource "google_storage_bucket" "static_assets" {
  name     = "my-app-static-assets"
  location = "US"

  uniform_bucket_level_access = true
}

resource "google_storage_bucket" "backups" {
  name     = "my-app-database-backups"
  location = "US"
}

# ── Compute ───────────────────────────────────────────────────────────────────

# Web/API server — e2-standard is GCP's general-purpose workhorse
resource "google_compute_instance" "api_server" {
  name         = "api-server"
  machine_type = "e2-standard-2"
  zone         = "us-east1-b"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network = "default"
    access_config {}
  }

  tags = ["http-server", "https-server"]
}

# Background processor — slightly larger for CPU-bound tasks
resource "google_compute_instance" "worker" {
  name         = "background-worker"
  machine_type = "e2-standard-4"
  zone         = "us-east1-b"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network = "default"
  }

  tags = ["worker"]
}

# Cache/session server — memory optimized workload
resource "google_compute_instance" "cache" {
  name         = "cache-server"
  machine_type = "n2-standard-2"
  zone         = "us-east1-b"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network = "default"
  }
}

# ── Database ──────────────────────────────────────────────────────────────────

resource "google_sql_database_instance" "main" {
  name             = "app-postgres"
  database_version = "POSTGRES_15"
  region           = "us-east1"

  settings {
    tier            = "db-n1-standard-2"
    disk_size       = 100
    disk_type       = "PD_SSD"
    availability_type = "REGIONAL"
  }

  deletion_protection = true
}
