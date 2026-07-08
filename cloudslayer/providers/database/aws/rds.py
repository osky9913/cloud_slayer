"""AWS RDS database provider — prices fetched live from the AWS Pricing API."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import requests

from ....config import fallback_prices_enabled, force_live_prices_enabled, get_aws_region
from ....pricing import PricingUnavailableError
from ..base import DatabasePlan, DatabaseProvider

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600  # 7 days

# Instance specs: name → (vcpu, memory_gb)
_PLAN_SPECS: dict[str, tuple[int, float]] = {
    # db.t3
    "db.t3.micro": (2, 1.0),
    "db.t3.small": (2, 2.0),
    "db.t3.medium": (2, 4.0),
    "db.t3.large": (2, 8.0),
    "db.t3.xlarge": (4, 16.0),
    "db.t3.2xlarge": (8, 32.0),
    # db.t4g (Graviton 2)
    "db.t4g.micro": (2, 1.0),
    "db.t4g.small": (2, 2.0),
    "db.t4g.medium": (2, 4.0),
    "db.t4g.large": (2, 8.0),
    "db.t4g.xlarge": (4, 16.0),
    "db.t4g.2xlarge": (8, 32.0),
    # db.m5
    "db.m5.large": (2, 8.0),
    "db.m5.xlarge": (4, 16.0),
    "db.m5.2xlarge": (8, 32.0),
    # db.m6i (6th gen Intel)
    "db.m6i.large": (2, 8.0),
    "db.m6i.xlarge": (4, 16.0),
    "db.m6i.2xlarge": (8, 32.0),
    "db.m6i.4xlarge": (16, 64.0),
    # db.m6g (Graviton 2)
    "db.m6g.large": (2, 8.0),
    "db.m6g.xlarge": (4, 16.0),
    "db.m6g.2xlarge": (8, 32.0),
    # db.r5
    "db.r5.large": (2, 16.0),
    "db.r5.xlarge": (4, 32.0),
    "db.r5.2xlarge": (8, 64.0),
    # db.r6i (memory optimized 6th gen)
    "db.r6i.large": (2, 16.0),
    "db.r6i.xlarge": (4, 32.0),
    "db.r6i.2xlarge": (8, 64.0),
    # db.r6g (Graviton 2 memory)
    "db.r6g.large": (2, 16.0),
    "db.r6g.xlarge": (4, 32.0),
    "db.r6g.2xlarge": (8, 64.0),
}

_STORAGE_PER_GB = 0.115  # gp3 storage, us-east-1 — stable
_INCLUDED_STORAGE_GB = 0.0

# Fallback instance prices (us-east-1, PostgreSQL, Single-AZ) — verified 2026-07
_FALLBACK_PRICES: dict[str, float] = {
    # db.t3
    "db.t3.micro": 12.41,
    "db.t3.small": 24.82,
    "db.t3.medium": 49.64,
    "db.t3.large": 99.28,
    "db.t3.xlarge": 198.56,
    "db.t3.2xlarge": 397.12,
    # db.t4g (Graviton 2)
    "db.t4g.micro": 10.55,
    "db.t4g.small": 21.11,
    "db.t4g.medium": 42.22,
    "db.t4g.large": 84.43,
    "db.t4g.xlarge": 168.86,
    "db.t4g.2xlarge": 337.72,
    # db.m5
    "db.m5.large": 131.40,
    "db.m5.xlarge": 262.80,
    "db.m5.2xlarge": 525.60,
    # db.m6i
    "db.m6i.large": 133.58,
    "db.m6i.xlarge": 267.15,
    "db.m6i.2xlarge": 534.30,
    "db.m6i.4xlarge": 1068.60,
    # db.m6g
    "db.m6g.large": 113.54,
    "db.m6g.xlarge": 227.08,
    "db.m6g.2xlarge": 454.17,
    # db.r5
    "db.r5.large": 174.24,
    "db.r5.xlarge": 348.48,
    "db.r5.2xlarge": 696.96,
    # db.r6i
    "db.r6i.large": 175.30,
    "db.r6i.xlarge": 350.60,
    "db.r6i.2xlarge": 701.21,
    # db.r6g
    "db.r6g.large": 149.01,
    "db.r6g.xlarge": 298.01,
    "db.r6g.2xlarge": 596.02,
}


def _notes(name: str) -> str:
    family = name.split(".")[1] if "." in name else ""
    return {
        "t3": "Burstable, us-east-1",
        "t4g": "Burstable, Graviton 2, us-east-1",
        "m5": "General purpose, us-east-1",
        "m6i": "General purpose, 6th gen Intel, us-east-1",
        "m6g": "General purpose, Graviton 2, us-east-1",
        "r5": "Memory optimized, us-east-1",
        "r6i": "Memory optimized, 6th gen Intel, us-east-1",
        "r6g": "Memory optimized, Graviton 2, us-east-1",
    }.get(family, "PostgreSQL, us-east-1")


_PLANS = [
    DatabasePlan(
        name,
        vcpu,
        mem,
        _FALLBACK_PRICES[name],
        _STORAGE_PER_GB,
        _INCLUDED_STORAGE_GB,
        _notes(name),
        "fallback",
        "https://aws.amazon.com/rds/postgresql/pricing/",
    )
    for name, (vcpu, mem) in _PLAN_SPECS.items()
]


class AWSRDSProvider(DatabaseProvider):
    @property
    def name(self) -> str:
        return "aws_rds"

    @property
    def display_name(self) -> str:
        return "AWS RDS"

    def plans(self) -> list[DatabasePlan]:
        try:
            return self._live_plans()
        except Exception as error:
            if fallback_prices_enabled():
                return _PLANS
            raise PricingUnavailableError(
                self.display_name,
                f"live pricing unavailable ({error}); rerun with --fallback to use verified static AWS prices",
            ) from error

    def _live_plans(self) -> list[DatabasePlan]:
        region = get_aws_region()
        cache_file = CACHE_DIR / f"aws_rds_prices_{region}.json"
        if (
            not force_live_prices_enabled()
            and cache_file.exists()
            and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL
        ):
            with open(cache_file) as f:
                cached = json.load(f)
            if "storage_per_gb" in cached:
                return _build_plans(cached, "cache")
        try:
            return self._fetch_and_cache(cache_file)
        except Exception:
            if (
                not force_live_prices_enabled()
                and cache_file.exists()
                and fallback_prices_enabled()
            ):
                with open(cache_file) as f:
                    return _build_plans(json.load(f), "stale cache")
            raise

    def _fetch_and_cache(self, cache_file: Path) -> list[DatabasePlan]:
        region = get_aws_region()
        url = (
            f"https://pricing.us-east-1.amazonaws.com"
            f"/offers/v1.0/aws/AmazonRDS/current/{region}/index.json"
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
            pricing = _extract_rds_prices(data)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w") as f:
                json.dump(pricing, f)
            return _build_plans(pricing, "live")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass


def _extract_rds_prices(data: dict) -> dict:
    target = set(_PLAN_SPECS)
    sku_to_class: dict[str, str] = {}
    storage_skus: list[str] = []
    for sku, product in data.get("products", {}).items():
        attrs = product.get("attributes", {})
        if (
            product.get("productFamily") == "Database Storage"
            and attrs.get("databaseEngine") == "PostgreSQL"
            and "gp3" in attrs.get("volumeType", "").lower()
            and attrs.get("deploymentOption") == "Single-AZ"
        ):
            storage_skus.append(sku)
            continue
        if product.get("productFamily") != "Database Instance":
            continue
        iclass = attrs.get("instanceType", "")
        if (
            iclass not in target
            or attrs.get("databaseEngine") != "PostgreSQL"
            or attrs.get("deploymentOption") != "Single-AZ"
        ):
            continue
        sku_to_class[sku] = iclass

    prices: dict[str, float] = {}
    on_demand = data.get("terms", {}).get("OnDemand", {})
    for sku, iclass in sku_to_class.items():
        if iclass in prices:
            continue
        for term_val in on_demand.get(sku, {}).values():
            for dim in term_val.get("priceDimensions", {}).values():
                if dim.get("unit") == "Hrs":
                    hourly = float(dim["pricePerUnit"].get("USD", 0))
                    if hourly > 0:
                        prices[iclass] = round(hourly * 730, 2)
                        break
    storage_per_gb = 0.0
    for sku in storage_skus:
        for term_val in on_demand.get(sku, {}).values():
            for dim in term_val.get("priceDimensions", {}).values():
                if dim.get("unit") in ("GB-Mo", "GB-month"):
                    storage_per_gb = float(dim.get("pricePerUnit", {}).get("USD", 0))
                    if storage_per_gb > 0:
                        break
            if storage_per_gb > 0:
                break
        if storage_per_gb > 0:
            break
    return {"prices": prices, "storage_per_gb": storage_per_gb}


def _build_plans(data: dict, source: str = "live") -> list[DatabasePlan]:
    prices = data.get("prices", data)
    storage = float(data.get("storage_per_gb", 0))
    if storage <= 0 and not fallback_prices_enabled():
        raise ValueError("AWS price file did not contain PostgreSQL gp3 storage pricing")
    storage = storage or _STORAGE_PER_GB
    return [
        DatabasePlan(
            name,
            vcpu,
            mem,
            prices.get(name, _FALLBACK_PRICES[name]),
            storage,
            _INCLUDED_STORAGE_GB,
            _notes(name),
            source if name in prices and data.get("storage_per_gb", 0) else "mixed fallback",
            "https://aws.amazon.com/rds/postgresql/pricing/",
        )
        for name, (vcpu, mem) in _PLAN_SPECS.items()
        if name in prices or fallback_prices_enabled()
    ]
