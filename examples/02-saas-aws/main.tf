# Scenario: Mid-size SaaS product on AWS — typical $1k+/mo infrastructure.
# Multiple services: API, workers, ML batch job, S3 for files + logs, RDS cluster.
# High savings potential: reserved instances, ARM migration, full provider comparison.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = "us-east-1"
}

# ── Storage ───────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "user_files" {
  bucket = "saas-user-files"
  tags   = { Purpose = "user-uploads" }
}

resource "aws_s3_bucket" "logs" {
  bucket = "saas-application-logs"
  tags   = { Purpose = "logs" }
}

resource "aws_s3_bucket" "ml_models" {
  bucket = "saas-ml-model-artifacts"
  tags   = { Purpose = "ml" }
}

# ── Compute ───────────────────────────────────────────────────────────────────

# API servers — general purpose, always on
resource "aws_instance" "api_primary" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "m5.xlarge"
  tags          = { Name = "api-primary", Role = "api" }
}

resource "aws_instance" "api_secondary" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "m5.xlarge"
  tags          = { Name = "api-secondary", Role = "api" }
}

# Async job workers — CPU-intensive queue processing
resource "aws_instance" "worker_heavy" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "c5.2xlarge"
  tags          = { Name = "worker-heavy", Role = "queue" }
}

resource "aws_instance" "worker_light" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "c5.large"
  tags          = { Name = "worker-light", Role = "queue" }
}

# Batch ML inference — memory-intensive
resource "aws_instance" "ml_inference" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "r5.xlarge"
  tags          = { Name = "ml-inference", Role = "ml" }
}

# ── Database ──────────────────────────────────────────────────────────────────

# Primary application database
resource "aws_db_instance" "app_db" {
  identifier        = "saas-app-db"
  engine            = "postgres"
  engine_version    = "15"
  instance_class    = "db.m5.large"
  allocated_storage = 200

  db_name  = "saasapp"
  username = "admin"
  password = "stored-in-secrets-manager"

  multi_az            = true
  skip_final_snapshot = false
}

# Analytics read replica — separate instance for BI queries
resource "aws_db_instance" "analytics_db" {
  identifier        = "saas-analytics-db"
  engine            = "postgres"
  engine_version    = "15"
  instance_class    = "db.t3.xlarge"
  allocated_storage = 500

  db_name  = "analytics"
  username = "readonly"
  password = "stored-in-secrets-manager"

  skip_final_snapshot = true
}
