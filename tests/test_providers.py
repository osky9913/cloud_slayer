"""Tests for individual provider pricing logic."""

import pytest

from cloudslayer.models import ComputeSpec, DatabaseSpec, ObjectStorageSpec
from cloudslayer.providers.compute.aws.ec2 import AWSEC2Provider
from cloudslayer.providers.compute.azure.vm import AzureComputeProvider
from cloudslayer.providers.compute.gcp.gce import GCPComputeProvider
from cloudslayer.providers.database.aws.rds import AWSRDSProvider
from cloudslayer.providers.database.azure.postgres import AzurePostgresProvider
from cloudslayer.providers.database.gcp.cloudsql import GCPCloudSQLProvider
from cloudslayer.providers.storage.aws.s3 import AWSS3Provider
from cloudslayer.providers.storage.azure.blob import AzureBlobProvider
from cloudslayer.providers.storage.gcp.storage import GCPStorageProvider

# ── Compute: AWS EC2 ──────────────────────────────────────────────────────────


class TestAWSEC2:
    def setup_method(self):
        self.provider = AWSEC2Provider()

    def test_t3_medium_price(self):
        spec = ComputeSpec(name="x", vcpu=2, memory_gb=4)
        result = self.provider.calculate_cost(spec)
        assert result is not None
        assert "t3" in result.instance_name.lower() or result.price_per_month > 20

    def test_provider_name(self):
        assert self.provider.name == "aws_ec2"

    def test_no_match_returns_none(self):
        spec = ComputeSpec(name="x", vcpu=9999, memory_gb=9999)
        assert self.provider.calculate_cost(spec) is None


# ── Compute: GCP ──────────────────────────────────────────────────────────────


class TestGCPCompute:
    def setup_method(self):
        self.provider = GCPComputeProvider()

    def test_e2_medium_price(self):
        spec = ComputeSpec(name="x", vcpu=2, memory_gb=4)
        result = self.provider.calculate_cost(spec)
        assert result is not None
        assert result.price_per_month == pytest.approx(24.11, abs=0.01)

    def test_provider_name(self):
        assert self.provider.name == "gcp_gce"


# ── Compute: Azure ────────────────────────────────────────────────────────────


class TestAzureCompute:
    def setup_method(self):
        self.provider = AzureComputeProvider()

    def test_has_result_for_standard_spec(self):
        spec = ComputeSpec(name="x", vcpu=2, memory_gb=4)
        result = self.provider.calculate_cost(spec)
        assert result is not None
        assert result.price_per_month > 0

    def test_provider_name(self):
        assert self.provider.name == "azure_vm"


# ── Database: AWS RDS ─────────────────────────────────────────────────────────


class TestAWSRDS:
    def setup_method(self):
        self.provider = AWSRDSProvider()

    def test_has_result_for_standard_spec(self):
        spec = DatabaseSpec(name="x", vcpu=2, memory_gb=4, storage_gb=20)
        result = self.provider.calculate_cost(spec)
        assert result is not None
        assert result.total > 0

    def test_provider_name(self):
        assert self.provider.name == "aws_rds"


# ── Database: GCP Cloud SQL ───────────────────────────────────────────────────


class TestGCPCloudSQL:
    def setup_method(self):
        self.provider = GCPCloudSQLProvider()

    def test_has_result_for_standard_spec(self):
        spec = DatabaseSpec(name="x", vcpu=2, memory_gb=4, storage_gb=20)
        result = self.provider.calculate_cost(spec)
        assert result is not None
        assert result.total > 0

    def test_provider_name(self):
        assert self.provider.name == "gcp_cloudsql"


# ── Database: Azure PostgreSQL ────────────────────────────────────────────────


class TestAzurePostgres:
    def setup_method(self):
        self.provider = AzurePostgresProvider()

    def test_has_result_for_standard_spec(self):
        spec = DatabaseSpec(name="x", vcpu=2, memory_gb=4, storage_gb=20)
        result = self.provider.calculate_cost(spec)
        assert result is not None
        assert result.total > 0

    def test_storage_billed_from_zero_included(self):
        small = self.provider.calculate_cost(
            DatabaseSpec(name="x", vcpu=1, memory_gb=2, storage_gb=0)
        )
        big = self.provider.calculate_cost(
            DatabaseSpec(name="x", vcpu=1, memory_gb=2, storage_gb=100)
        )
        assert big.storage_cost > small.storage_cost

    def test_provider_name(self):
        assert self.provider.name == "azure_db"


# ── Object Storage: AWS S3 ────────────────────────────────────────────────────


class TestAWSS3:
    def setup_method(self):
        self.provider = AWSS3Provider()

    def test_egress_cost(self):
        spec = ObjectStorageSpec(name="x", storage_gb=0, egress_gb=200)
        result = self.provider.calculate_cost(spec)
        assert result.egress_cost > 0

    def test_provider_name(self):
        assert self.provider.name == "aws_s3"


# ── Object Storage: GCP ───────────────────────────────────────────────────────


class TestGCPStorage:
    def setup_method(self):
        self.provider = GCPStorageProvider()

    def test_storage_cost(self):
        spec = ObjectStorageSpec(name="x", storage_gb=1000, egress_gb=0)
        result = self.provider.calculate_cost(spec)
        assert result.storage_cost > 0

    def test_provider_name(self):
        assert self.provider.name == "gcp_storage"


# ── Object Storage: Azure Blob ────────────────────────────────────────────────


class TestAzureBlob:
    def setup_method(self):
        self.provider = AzureBlobProvider()

    def test_cheaper_than_aws_at_high_volume(self):
        spec = ObjectStorageSpec(name="x", storage_gb=5000, egress_gb=0)
        azure = self.provider.calculate_cost(spec)
        aws = AWSS3Provider().calculate_cost(spec)
        assert azure.total < aws.total

    def test_provider_name(self):
        assert self.provider.name == "azure_blob"
