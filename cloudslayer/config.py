"""Region configuration for cloudslayer."""

from __future__ import annotations

import os

# AWS region → (GCP region, Azure region)
REGION_MAP: dict[str, tuple[str, str]] = {
    "us-east-1": ("us-east1", "eastus"),
    "us-east-2": ("us-east4", "eastus2"),
    "us-west-1": ("us-west1", "westus"),
    "us-west-2": ("us-west2", "westus2"),
    "eu-west-1": ("europe-west1", "westeurope"),
    "eu-west-2": ("europe-west2", "uksouth"),
    "eu-central-1": ("europe-west3", "germanywestcentral"),
    "eu-north-1": ("europe-north1", "swedencentral"),
    "ap-southeast-1": ("asia-southeast1", "southeastasia"),
    "ap-northeast-1": ("asia-northeast1", "japaneast"),
    "ap-south-1": ("asia-south1", "centralindia"),
    "ca-central-1": ("northamerica-northeast1", "canadacentral"),
    "sa-east-1": ("southamerica-east1", "brazilsouth"),
}

_DEFAULT = "us-east-1"


def get_aws_region() -> str:
    return os.environ.get("CLOUDSLAYER_REGION", _DEFAULT)


def get_gcp_region() -> str:
    aws = get_aws_region()
    return REGION_MAP.get(aws, REGION_MAP[_DEFAULT])[0]


def get_azure_region() -> str:
    aws = get_aws_region()
    return REGION_MAP.get(aws, REGION_MAP[_DEFAULT])[1]


def set_region(aws_region: str) -> None:
    """Set the active region (used by CLI --region flag)."""
    os.environ["CLOUDSLAYER_REGION"] = aws_region
