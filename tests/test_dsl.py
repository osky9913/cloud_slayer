"""Tests for HCL parsing — covers all three resource block types."""
import textwrap
import tempfile
import os
import pytest

from cloudslayer.dsl import parse_hcl


def _write_hcl(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".hcl", delete=False)
    f.write(textwrap.dedent(content))
    f.close()
    return f.name


def test_parse_object_storage():
    path = _write_hcl("""
        object_storage "my-bucket" {
          storage_gb   = 500
          get_requests = 5000000
          put_requests = 500000
          egress_gb    = 200
        }
    """)
    storage, compute, database = parse_hcl(path)
    os.unlink(path)

    assert len(storage) == 1
    s = storage[0]
    assert s.name == "my-bucket"
    assert s.storage_gb == 500.0
    assert s.get_requests == 5_000_000
    assert s.put_requests == 500_000
    assert s.egress_gb == 200.0
    assert compute == []
    assert database == []


def test_parse_compute():
    path = _write_hcl("""
        compute "api-server" {
          vcpu      = 2
          memory_gb = 4
        }
    """)
    storage, compute, database = parse_hcl(path)
    os.unlink(path)

    assert len(compute) == 1
    c = compute[0]
    assert c.name == "api-server"
    assert c.vcpu == 2
    assert c.memory_gb == 4.0
    assert storage == []
    assert database == []


def test_parse_database():
    path = _write_hcl("""
        database "main-db" {
          vcpu       = 2
          memory_gb  = 4
          storage_gb = 20
          engine     = "postgres"
        }
    """)
    storage, compute, database = parse_hcl(path)
    os.unlink(path)

    assert len(database) == 1
    d = database[0]
    assert d.name == "main-db"
    assert d.vcpu == 2
    assert d.memory_gb == 4.0
    assert d.storage_gb == 20.0
    assert d.engine == "postgres"
    assert storage == []
    assert compute == []


def test_parse_all_resource_types():
    path = _write_hcl("""
        object_storage "uploads" {
          storage_gb = 100
        }
        compute "worker" {
          vcpu      = 4
          memory_gb = 8
        }
        database "db" {
          vcpu       = 2
          memory_gb  = 4
          storage_gb = 50
        }
    """)
    storage, compute, database = parse_hcl(path)
    os.unlink(path)

    assert len(storage) == 1
    assert len(compute) == 1
    assert len(database) == 1


def test_parse_multiple_blocks_same_type():
    path = _write_hcl("""
        object_storage "bucket-a" {
          storage_gb = 10
        }
        object_storage "bucket-b" {
          storage_gb = 20
        }
    """)
    storage, _, _ = parse_hcl(path)
    os.unlink(path)

    names = {s.name for s in storage}
    assert names == {"bucket-a", "bucket-b"}


def test_parse_defaults():
    path = _write_hcl("""
        object_storage "minimal" {
          storage_gb = 1
        }
    """)
    storage, _, _ = parse_hcl(path)
    os.unlink(path)

    s = storage[0]
    assert s.get_requests == 0
    assert s.put_requests == 0
    assert s.egress_gb == 0.0


def test_parse_empty_file():
    path = _write_hcl("")
    storage, compute, database = parse_hcl(path)
    os.unlink(path)

    assert storage == []
    assert compute == []
    assert database == []
