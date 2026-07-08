"""Tests for Terraform file scanner and spec generator."""

import json
import os
import tempfile
import textwrap

from cloudslayer.scanner import AWS_INSTANCE_SPECS, RESOURCE_MAP, generate_spec, scan, scan_path


def _write_tf(content: str, directory: str, filename: str = "main.tf") -> str:
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))
    return path


# ── scan() ────────────────────────────────────────────────────────────────────


def test_scan_detects_s3_bucket():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_s3_bucket" "uploads" {
              bucket = "my-uploads"
            }
        """,
            d,
        )
        resources = scan(d)

    assert len(resources) == 1
    r = resources[0]
    assert r.terraform_type == "aws_s3_bucket"
    assert r.resource_name == "uploads"
    assert r.cloudslayer_type == "object_storage"


def test_scan_detects_aws_instance():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_instance" "api_server" {
              ami           = "ami-12345"
              instance_type = "t3.medium"
            }
        """,
            d,
        )
        resources = scan(d)

    assert len(resources) == 1
    r = resources[0]
    assert r.terraform_type == "aws_instance"
    assert r.resource_name == "api_server"
    assert r.cloudslayer_type == "compute"


def test_scan_detects_rds_instance():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_db_instance" "main_db" {
              engine         = "postgres"
              instance_class = "db.t3.medium"
              allocated_storage = 100
            }
        """,
            d,
        )
        resources = scan(d)

    assert len(resources) == 1
    r = resources[0]
    assert r.terraform_type == "aws_db_instance"
    assert r.cloudslayer_type == "database"


def test_scan_ignores_unknown_resources():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_iam_role" "my_role" {
              name = "my-role"
            }
        """,
            d,
        )
        resources = scan(d)

    assert resources == []


def test_scan_mixed_resources():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_s3_bucket" "uploads" {
              bucket = "uploads"
            }
            resource "aws_instance" "web" {
              ami           = "ami-12345"
              instance_type = "t3.medium"
            }
            resource "aws_db_instance" "db" {
              engine         = "postgres"
              instance_class = "db.t3.medium"
              allocated_storage = 20
            }
            resource "aws_iam_role" "role" {
              name = "role"
            }
        """,
            d,
        )
        resources = scan(d)

    assert len(resources) == 3
    types = {r.cloudslayer_type for r in resources}
    assert types == {"object_storage", "compute", "database"}


def test_scan_multiple_files():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_s3_bucket" "bucket_a" {
              bucket = "a"
            }
        """,
            d,
            "storage.tf",
        )
        _write_tf(
            """
            resource "aws_instance" "web" {
              ami           = "ami-12345"
              instance_type = "t3.medium"
            }
        """,
            d,
            "compute.tf",
        )
        resources = scan(d)

    assert len(resources) == 2


def test_scan_empty_directory():
    with tempfile.TemporaryDirectory() as d:
        resources = scan(d)
    assert resources == []


def test_scan_skips_malformed_tf():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bad.tf")
        with open(path, "w") as f:
            f.write("this is not valid hcl {{{")
        _write_tf(
            """
            resource "aws_s3_bucket" "good" { bucket = "good" }
        """,
            d,
            "good.tf",
        )
        resources = scan(d)

    assert len(resources) == 1


# ── scan_path() with terraform plan JSON ─────────────────────────────────────


def _write_plan_json(directory: str, resources: list, child_modules: list = ()) -> str:
    plan = {
        "format_version": "1.2",
        "planned_values": {
            "root_module": {
                "resources": resources,
                "child_modules": list(child_modules),
            }
        },
    }
    path = os.path.join(directory, "plan.json")
    with open(path, "w") as f:
        json.dump(plan, f)
    return path


def _plan_resource(rtype: str, name: str, values: dict, index=None, mode="managed") -> dict:
    return {
        "address": f"{rtype}.{name}",
        "mode": mode,
        "type": rtype,
        "name": name,
        "index": index,
        "values": values,
    }


def test_plan_json_detects_instance():
    with tempfile.TemporaryDirectory() as d:
        path = _write_plan_json(
            d,
            [
                _plan_resource("aws_instance", "web", {"instance_type": "t3.medium"}),
            ],
        )
        report = scan_path(path)

    assert len(report.supported) == 1
    r = report.supported[0]
    assert r.cloudslayer_type == "compute"
    assert r.instance_label == "t3.medium"


def test_plan_json_walks_child_modules():
    with tempfile.TemporaryDirectory() as d:
        path = _write_plan_json(
            d,
            [],
            child_modules=[
                {
                    "address": "module.db",
                    "resources": [
                        _plan_resource(
                            "aws_db_instance",
                            "main",
                            {
                                "engine": "postgres",
                                "instance_class": "db.t3.medium",
                                "allocated_storage": 100,
                            },
                        ),
                    ],
                }
            ],
        )
        report = scan_path(path)

    assert len(report.supported) == 1
    assert report.supported[0].cloudslayer_type == "database"


def test_plan_json_expands_count_index():
    with tempfile.TemporaryDirectory() as d:
        path = _write_plan_json(
            d,
            [
                _plan_resource("aws_instance", "web", {"instance_type": "t3.small"}, index=0),
                _plan_resource("aws_instance", "web", {"instance_type": "t3.small"}, index=1),
            ],
        )
        report = scan_path(path)

    assert len(report.supported) == 2
    assert {r.resource_name for r in report.supported} == {"web-0", "web-1"}


def test_plan_json_skips_data_sources():
    with tempfile.TemporaryDirectory() as d:
        path = _write_plan_json(
            d,
            [
                _plan_resource("aws_instance", "web", {"instance_type": "t3.medium"}, mode="data"),
            ],
        )
        report = scan_path(path)

    assert report.supported == []


def test_plan_json_tracks_uncosted():
    with tempfile.TemporaryDirectory() as d:
        path = _write_plan_json(
            d,
            [
                _plan_resource("aws_nat_gateway", "nat", {}),
                _plan_resource("aws_iam_role", "role", {}),
            ],
        )
        report = scan_path(path)

    assert len(report.uncosted) == 1
    assert report.uncosted[0].terraform_type == "aws_nat_gateway"
    assert report.other_count == 1
    assert report.total_seen == 2


def test_scan_path_hcl_reports_uncosted():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_instance" "web" {
              instance_type = "t3.medium"
            }
            resource "aws_nat_gateway" "nat" {
              subnet_id = "subnet-1"
            }
            resource "aws_iam_role" "role" {
              name = "role"
            }
        """,
            d,
        )
        report = scan_path(d)

    assert len(report.supported) == 1
    assert len(report.uncosted) == 1
    assert report.other_count == 1


# ── generate_spec() ───────────────────────────────────────────────────────────


def test_generate_spec_object_storage():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_s3_bucket" "user_uploads" { bucket = "uploads" }
        """,
            d,
        )
        resources = scan(d)

    spec = generate_spec(resources)
    assert 'object_storage "user-uploads"' in spec
    assert "storage_gb" in spec
    assert "FIXME" in spec


def test_generate_spec_compute_with_instance_type():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_instance" "worker" {
              ami           = "ami-12345"
              instance_type = "t3.xlarge"
            }
        """,
            d,
        )
        resources = scan(d)

    spec = generate_spec(resources)
    assert 'compute "worker"' in spec
    # t3.xlarge = 4 vCPU, 16 GB
    assert "vcpu      = 4" in spec
    assert "memory_gb = 16.0" in spec


def test_generate_spec_database_with_storage():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_db_instance" "main_db" {
              engine            = "postgres"
              instance_class    = "db.t3.medium"
              allocated_storage = 100
            }
        """,
            d,
        )
        resources = scan(d)

    spec = generate_spec(resources)
    assert 'database "main-db"' in spec
    assert "storage_gb = 100" in spec
    # db.t3.medium = 2 vCPU, 4 GB
    assert "vcpu       = 2" in spec


def test_generate_spec_underscore_to_hyphen():
    with tempfile.TemporaryDirectory() as d:
        _write_tf(
            """
            resource "aws_s3_bucket" "my_bucket_name" { bucket = "x" }
        """,
            d,
        )
        resources = scan(d)

    spec = generate_spec(resources)
    assert '"my-bucket-name"' in spec


# ── RESOURCE_MAP and AWS_INSTANCE_SPECS ───────────────────────────────────────


def test_resource_map_covers_major_providers():
    assert "aws_s3_bucket" in RESOURCE_MAP
    assert "aws_instance" in RESOURCE_MAP
    assert "aws_db_instance" in RESOURCE_MAP
    assert "google_compute_instance" in RESOURCE_MAP
    assert "azurerm_linux_virtual_machine" in RESOURCE_MAP


def test_instance_specs_t3_medium():
    vcpu, mem = AWS_INSTANCE_SPECS["t3.medium"]
    assert vcpu == 2
    assert mem == 4.0


def test_instance_specs_t3_xlarge():
    vcpu, mem = AWS_INSTANCE_SPECS["t3.xlarge"]
    assert vcpu == 4
    assert mem == 16.0
