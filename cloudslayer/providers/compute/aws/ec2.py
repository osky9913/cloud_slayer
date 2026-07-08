"""AWS EC2 compute provider — prices fetched live from the AWS Pricing API."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import requests

from ....config import get_aws_region
from ..base import ComputeProvider, InstanceType

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600  # 7 days

# Instance specs: name → (vcpu, memory_gb)  — stable; only prices fetched live
_INSTANCE_SPECS: dict[str, tuple[int, float]] = {
    # t3 (burstable)
    "t3.nano": (2, 0.5),
    "t3.micro": (2, 1.0),
    "t3.small": (2, 2.0),
    "t3.medium": (2, 4.0),
    "t3.large": (2, 8.0),
    "t3.xlarge": (4, 16.0),
    "t3.2xlarge": (8, 32.0),
    # t4g (Graviton 2, burstable)
    "t4g.nano": (2, 0.5),
    "t4g.micro": (2, 1.0),
    "t4g.small": (2, 2.0),
    "t4g.medium": (2, 4.0),
    "t4g.large": (2, 8.0),
    "t4g.xlarge": (4, 16.0),
    "t4g.2xlarge": (8, 32.0),
    # m5 (general purpose)
    "m5.large": (2, 8.0),
    "m5.xlarge": (4, 16.0),
    "m5.2xlarge": (8, 32.0),
    "m5.4xlarge": (16, 64.0),
    # m6i (6th gen Intel, general purpose)
    "m6i.large": (2, 8.0),
    "m6i.xlarge": (4, 16.0),
    "m6i.2xlarge": (8, 32.0),
    "m6i.4xlarge": (16, 64.0),
    # m6g (Graviton 2, general purpose)
    "m6g.large": (2, 8.0),
    "m6g.xlarge": (4, 16.0),
    "m6g.2xlarge": (8, 32.0),
    "m6g.4xlarge": (16, 64.0),
    # c5 (compute optimized)
    "c5.large": (2, 4.0),
    "c5.xlarge": (4, 8.0),
    "c5.2xlarge": (8, 16.0),
    "c5.4xlarge": (16, 32.0),
    # c6i (6th gen Intel, compute optimized)
    "c6i.large": (2, 4.0),
    "c6i.xlarge": (4, 8.0),
    "c6i.2xlarge": (8, 16.0),
    "c6i.4xlarge": (16, 32.0),
    # c6g (Graviton 2, compute optimized)
    "c6g.large": (2, 4.0),
    "c6g.xlarge": (4, 8.0),
    "c6g.2xlarge": (8, 16.0),
    "c6g.4xlarge": (16, 32.0),
    # r5 (memory optimized)
    "r5.large": (2, 16.0),
    "r5.xlarge": (4, 32.0),
    "r5.2xlarge": (8, 64.0),
    "r5.4xlarge": (16, 128.0),
    # r6i (6th gen Intel, memory optimized)
    "r6i.large": (2, 16.0),
    "r6i.xlarge": (4, 32.0),
    "r6i.2xlarge": (8, 64.0),
    "r6i.4xlarge": (16, 128.0),
    # r6g (Graviton 2, memory optimized)
    "r6g.large": (2, 16.0),
    "r6g.xlarge": (4, 32.0),
    "r6g.2xlarge": (8, 64.0),
    # i3 (NVMe storage optimized)
    "i3.large": (2, 15.25),
    "i3.xlarge": (4, 30.5),
    "i3.2xlarge": (8, 61.0),
    # g4dn (GPU inference)
    "g4dn.xlarge": (4, 16.0),
    "g4dn.2xlarge": (8, 32.0),
}

# Fallback prices (us-east-1, Linux, on-demand) — verified 2026-07
_FALLBACK_PRICES: dict[str, float] = {
    # t3
    "t3.nano": 3.74,
    "t3.micro": 7.59,
    "t3.small": 15.18,
    "t3.medium": 30.37,
    "t3.large": 60.74,
    "t3.xlarge": 121.47,
    "t3.2xlarge": 242.94,
    # t4g
    "t4g.nano": 3.07,
    "t4g.micro": 6.11,
    "t4g.small": 12.26,
    "t4g.medium": 24.53,
    "t4g.large": 49.06,
    "t4g.xlarge": 98.13,
    "t4g.2xlarge": 196.25,
    # m5
    "m5.large": 70.08,
    "m5.xlarge": 140.16,
    "m5.2xlarge": 280.32,
    "m5.4xlarge": 560.64,
    # m6i
    "m6i.large": 76.65,
    "m6i.xlarge": 153.31,
    "m6i.2xlarge": 306.62,
    "m6i.4xlarge": 613.24,
    # m6g
    "m6g.large": 65.15,
    "m6g.xlarge": 130.29,
    "m6g.2xlarge": 260.59,
    "m6g.4xlarge": 521.18,
    # c5
    "c5.large": 61.20,
    "c5.xlarge": 122.40,
    "c5.2xlarge": 244.80,
    "c5.4xlarge": 489.60,
    # c6i
    "c6i.large": 61.20,
    "c6i.xlarge": 122.40,
    "c6i.2xlarge": 244.80,
    "c6i.4xlarge": 489.60,
    # c6g
    "c6g.large": 52.02,
    "c6g.xlarge": 104.03,
    "c6g.2xlarge": 208.06,
    "c6g.4xlarge": 416.11,
    # r5
    "r5.large": 90.72,
    "r5.xlarge": 181.44,
    "r5.2xlarge": 362.88,
    "r5.4xlarge": 725.76,
    # r6i
    "r6i.large": 91.98,
    "r6i.xlarge": 183.96,
    "r6i.2xlarge": 367.92,
    "r6i.4xlarge": 735.84,
    # r6g
    "r6g.large": 78.19,
    "r6g.xlarge": 156.39,
    "r6g.2xlarge": 312.78,
    # i3
    "i3.large": 114.72,
    "i3.xlarge": 229.44,
    "i3.2xlarge": 458.88,
    # g4dn
    "g4dn.xlarge": 379.37,
    "g4dn.2xlarge": 599.76,
}


def _notes(name: str) -> str:
    prefix = name.split(".")[0]
    return {
        "t3": "Burstable",
        "t4g": "Burstable, Graviton 2",
        "m5": "General purpose",
        "m6i": "General purpose, 6th gen Intel",
        "m6g": "General purpose, Graviton 2",
        "c5": "Compute optimized",
        "c6i": "Compute optimized, 6th gen Intel",
        "c6g": "Compute optimized, Graviton 2",
        "r5": "Memory optimized",
        "r6i": "Memory optimized, 6th gen Intel",
        "r6g": "Memory optimized, Graviton 2",
        "i3": "Storage optimized, NVMe",
        "g4dn": "GPU inference",
    }.get(prefix, "")


_CATALOG = [
    InstanceType(name, vcpu, mem, _FALLBACK_PRICES[name], _notes(name))
    for name, (vcpu, mem) in _INSTANCE_SPECS.items()
]


class AWSEC2Provider(ComputeProvider):
    @property
    def name(self) -> str:
        return "aws_ec2"

    @property
    def display_name(self) -> str:
        return "AWS EC2"

    def catalog(self) -> list[InstanceType]:
        try:
            return self._live_catalog()
        except Exception:
            return _CATALOG

    def _live_catalog(self) -> list[InstanceType]:
        region = get_aws_region()
        cache_file = CACHE_DIR / f"aws_ec2_prices_{region}.json"
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL:
            with open(cache_file) as f:
                return _build_catalog(json.load(f))
        try:
            return self._fetch_and_cache(cache_file)
        except Exception:
            if cache_file.exists():
                with open(cache_file) as f:
                    return _build_catalog(json.load(f))
            raise

    def _fetch_and_cache(self, cache_file: Path) -> list[InstanceType]:
        region = get_aws_region()
        url = (
            f"https://pricing.us-east-1.amazonaws.com"
            f"/offers/v1.0/aws/AmazonEC2/current/{region}/index.json"
        )
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                tmp_path = tmp.name
            resp = requests.get(url, stream=True, timeout=300)
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            with open(tmp_path) as f:
                data = json.load(f)
            prices = _extract_ec2_prices(data)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w") as f:
                json.dump(prices, f)
            return _build_catalog(prices)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass


def _extract_ec2_prices(data: dict) -> dict[str, float]:
    target = set(_INSTANCE_SPECS)
    sku_to_type: dict[str, str] = {}
    for sku, product in data.get("products", {}).items():
        if product.get("productFamily") != "Compute Instance":
            continue
        attrs = product.get("attributes", {})
        itype = attrs.get("instanceType", "")
        if (
            itype not in target
            or attrs.get("operatingSystem") != "Linux"
            or attrs.get("tenancy") != "Shared"
            or attrs.get("preInstalledSw") != "NA"
            or attrs.get("capacityStatus") == "AllocatedCapacityReservation"
        ):
            continue
        sku_to_type[sku] = itype

    prices: dict[str, float] = {}
    on_demand = data.get("terms", {}).get("OnDemand", {})
    for sku, itype in sku_to_type.items():
        if itype in prices:
            continue
        for term_val in on_demand.get(sku, {}).values():
            for dim in term_val.get("priceDimensions", {}).values():
                if dim.get("unit") == "Hrs":
                    hourly = float(dim["pricePerUnit"].get("USD", 0))
                    if hourly > 0:
                        prices[itype] = round(hourly * 730, 2)
                        break
    return prices


def _build_catalog(prices: dict[str, float]) -> list[InstanceType]:
    return [
        InstanceType(name, vcpu, mem, prices.get(name, _FALLBACK_PRICES[name]), _notes(name))
        for name, (vcpu, mem) in _INSTANCE_SPECS.items()
    ]
