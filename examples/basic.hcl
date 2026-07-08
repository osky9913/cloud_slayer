# cloudslayer — infrastructure cost spec
# Run: cloudslayer plan examples/basic.hcl

object_storage "user-uploads" {
  storage_gb   = 500
  get_requests = 5000000
  put_requests = 500000
  egress_gb    = 200
}

object_storage "backups" {
  storage_gb   = 2000
  get_requests = 5000
  put_requests = 10000
  egress_gb    = 10
}

compute "api-server" {
  vcpu      = 2
  memory_gb = 4
}

compute "worker" {
  vcpu      = 4
  memory_gb = 8
}

database "main-db" {
  vcpu       = 2
  memory_gb  = 4
  storage_gb = 20
  engine     = "postgres"
}
