from __future__ import annotations

import pytest

from cloudslayer.config import set_fallback_prices, set_force_live_prices
from cloudslayer.models import StoragePricing
from cloudslayer.providers.compute.aws.ec2 import AWSEC2Provider
from cloudslayer.providers.compute.azure.vm import AzureComputeProvider
from cloudslayer.providers.compute.base import InstanceType
from cloudslayer.providers.compute.gcp.gce import GCPComputeProvider
from cloudslayer.providers.database.aws.rds import AWSRDSProvider
from cloudslayer.providers.database.azure.postgres import AzurePostgresProvider
from cloudslayer.providers.database.base import DatabasePlan
from cloudslayer.providers.database.gcp.cloudsql import GCPCloudSQLProvider
from cloudslayer.providers.storage.aws.s3 import AWSS3Provider
from cloudslayer.providers.storage.azure.blob import AzureBlobProvider
from cloudslayer.providers.storage.gcp.storage import GCPStorageProvider


def _offline(*_args, **_kwargs):
    raise OSError("offline test")


@pytest.fixture(autouse=True)
def deterministic_pricing(monkeypatch):
    """Keep unit tests offline and make every selected source explicit."""
    set_fallback_prices(True)
    set_force_live_prices(False)
    monkeypatch.setattr(AWSEC2Provider, "_live_catalog", _offline)
    monkeypatch.setattr(AzureComputeProvider, "_live_catalog", _offline)
    monkeypatch.setattr(AWSRDSProvider, "_live_plans", _offline)
    monkeypatch.setattr(AzurePostgresProvider, "_live_plans", _offline)
    monkeypatch.setattr(AWSS3Provider, "_load_or_fetch", _offline)
    monkeypatch.setattr(AzureBlobProvider, "_load_or_fetch", _offline)

    monkeypatch.setattr(
        GCPComputeProvider,
        "_live_catalog",
        lambda _self: [
            InstanceType("e2-medium", 2, 4.0, 24.11, "Shared vCPU", "live"),
            InstanceType("e2-standard-4", 4, 16.0, 97.85, "Standard", "live"),
        ],
    )
    monkeypatch.setattr(
        GCPStorageProvider,
        "_live_pricing",
        lambda _self: StoragePricing(
            "gcp_storage",
            "GCP Cloud Storage",
            0.020,
            0.40,
            5.00,
            0.12,
            price_source="live",
        ),
    )
    monkeypatch.setattr(
        GCPCloudSQLProvider,
        "_live_plans",
        lambda _self: [
            DatabasePlan("db-g1-small", 1, 1.7, 25.14, 0.17, price_source="live"),
            DatabasePlan("db-n1-standard-2", 2, 7.5, 93.14, 0.17, price_source="live"),
        ],
    )
    yield
    set_fallback_prices(False)
    set_force_live_prices(False)
