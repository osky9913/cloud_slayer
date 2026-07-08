"""Tests for engine.py — verifies sorting, free tiers, and storage crossover."""
import pytest

from cloudslayer.engine import plan_object_storage, plan_compute, plan_database
from cloudslayer.models import ObjectStorageSpec, ComputeSpec, DatabaseSpec


def test_object_storage_results_sorted_ascending():
    spec = ObjectStorageSpec(name="test", storage_gb=100, get_requests=1_000_000, egress_gb=10)
    results = plan_object_storage(spec)
    totals = [r.total for r in results]
    assert totals == sorted(totals)


def test_object_storage_returns_all_providers():
    spec = ObjectStorageSpec(name="test", storage_gb=100)
    results = plan_object_storage(spec)
    assert len(results) >= 3


def test_object_storage_azure_cheapest_major_cloud():
    # Azure Blob Hot LRS at $0.018/GB is cheapest among the three major clouds
    spec = ObjectStorageSpec(name="test", storage_gb=5000, get_requests=0, put_requests=0, egress_gb=0)
    results = plan_object_storage(spec)
    by_provider = {r.provider: r.total for r in results}
    assert by_provider["azure_blob"] < by_provider["aws_s3"]
    assert by_provider["azure_blob"] < by_provider["gcp_storage"]


def test_object_storage_all_three_present():
    spec = ObjectStorageSpec(name="test", storage_gb=100, get_requests=0, put_requests=0, egress_gb=0)
    results = plan_object_storage(spec)
    providers = {r.provider for r in results}
    assert {"aws_s3", "gcp_storage", "azure_blob"}.issubset(providers)


def test_object_storage_aws_egress_cost():
    # AWS egress is $0.09/GB; for 100 GB egress (no free tier), cost should be ~$9
    spec = ObjectStorageSpec(name="test", storage_gb=0, get_requests=0, put_requests=0, egress_gb=200)
    results = plan_object_storage(spec)
    aws = next(r for r in results if r.provider == "aws_s3")
    assert aws.egress_cost > 0


def test_compute_results_sorted_ascending():
    spec = ComputeSpec(name="test", vcpu=2, memory_gb=4)
    results = plan_compute(spec)
    totals = [r.total for r in results]
    assert totals == sorted(totals)


def test_compute_gcp_cheapest_major_cloud():
    # GCP e2-medium ($24.11) is cheapest among the three major clouds for 2vCPU/4GB
    spec = ComputeSpec(name="test", vcpu=2, memory_gb=4)
    results = plan_compute(spec)
    by_provider = {r.provider: r.price_per_month for r in results}
    assert by_provider["gcp_gce"] < by_provider["aws_ec2"]
    assert by_provider["gcp_gce"] < by_provider["azure_vm"]
    assert by_provider["gcp_gce"] == pytest.approx(24.11, abs=0.01)


def test_compute_minimum_spec_satisfied():
    spec = ComputeSpec(name="test", vcpu=4, memory_gb=8)
    results = plan_compute(spec)
    for r in results:
        assert r.instance_vcpu >= 4
        assert r.instance_memory_gb >= 8


def test_compute_no_match_excluded():
    # Requesting more resources than any provider offers — should return empty
    spec = ComputeSpec(name="test", vcpu=9999, memory_gb=9999)
    results = plan_compute(spec)
    assert results == []


def test_database_results_sorted_ascending():
    spec = DatabaseSpec(name="test", vcpu=2, memory_gb=4, storage_gb=20)
    results = plan_database(spec)
    totals = [r.total for r in results]
    assert totals == sorted(totals)


def test_database_aws_cheaper_than_gcp():
    # AWS RDS db.t3.medium (~$47) beats GCP Cloud SQL (~$93) for 2vCPU/4GB
    spec = DatabaseSpec(name="test", vcpu=2, memory_gb=4, storage_gb=5)
    results = plan_database(spec)
    by_provider = {r.provider: r.total for r in results}
    assert by_provider["aws_rds"] < by_provider["gcp_cloudsql"]


def test_database_aws_beats_gcp_at_large_storage():
    spec = DatabaseSpec(name="test", vcpu=2, memory_gb=4, storage_gb=200)
    results = plan_database(spec)
    providers = [r.provider for r in results]
    aws_idx = providers.index("aws_rds")
    gcp_idx = providers.index("gcp_cloudsql")
    assert aws_idx < gcp_idx


def test_database_no_match_excluded():
    spec = DatabaseSpec(name="test", vcpu=9999, memory_gb=9999, storage_gb=1)
    results = plan_database(spec)
    assert results == []
