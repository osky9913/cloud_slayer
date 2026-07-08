# Scenario: Early-stage startup on AWS us-east-1
# Two app servers, one S3 bucket, one small Postgres database.
# Good first target for Graviton (ARM) and reserved instance savings.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = "us-east-1"
}

# Object storage — assets and user uploads
resource "aws_s3_bucket" "assets" {
  bucket = "my-startup-assets"
  tags   = { Environment = "production" }
}

# App server — general-purpose workload
resource "aws_instance" "app" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t3.medium"

  tags = { Name = "app-server", Role = "api" }
}

# Background worker — slightly heavier CPU usage
resource "aws_instance" "worker" {
  ami           = "ami-0c55b159cbfafe1f0"
  instance_type = "t3.large"

  tags = { Name = "worker", Role = "jobs" }
}

# Postgres — small production database
resource "aws_db_instance" "postgres" {
  identifier        = "startup-db"
  engine            = "postgres"
  engine_version    = "15"
  instance_class    = "db.t3.small"
  allocated_storage = 50

  db_name  = "app"
  username = "postgres"
  password = "change-me-in-secrets-manager"

  skip_final_snapshot = true
}
