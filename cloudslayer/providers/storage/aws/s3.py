from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from ....config import get_aws_region
from ....models import StoragePricing
from ..base import ObjectStorageProvider

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600  # 7 days

_FALLBACK = StoragePricing(
    provider="aws_s3",
    display_name="AWS S3",
    storage_per_gb_mo=0.023,
    get_per_million=0.40,
    put_per_million=5.00,
    egress_per_gb=0.09,
    free_egress_gb=100.0,
    notes="us-east-1, Standard — cached values (2026-07-03)",
    source_url="https://aws.amazon.com/s3/pricing/",
    last_verified="2026-07-03",
)


class AWSS3Provider(ObjectStorageProvider):
    @property
    def name(self) -> str:
        return "aws_s3"

    @property
    def display_name(self) -> str:
        return "AWS S3"

    def get_pricing(self) -> StoragePricing:
        try:
            return self._load_or_fetch()
        except Exception:
            return _FALLBACK

    def _cache_path(self) -> Path:
        region = get_aws_region()
        return CACHE_DIR / f"aws_s3_{region}.json"

    def _load_or_fetch(self) -> StoragePricing:
        cache_file = self._cache_path()
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL:
            return self._parse_file(cache_file)
        return self._download_and_parse(cache_file)

    def _download_and_parse(self, cache_file: Path) -> StoragePricing:
        region = get_aws_region()
        pricing_url = (
            f"https://pricing.us-east-1.amazonaws.com"
            f"/offers/v1.0/aws/AmazonS3/current/{region}/index.json"
        )
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(pricing_url, timeout=60, stream=True)
        response.raise_for_status()
        with open(cache_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                f.write(chunk)
        return self._parse_file(cache_file)

    def _parse_file(self, path: Path) -> StoragePricing:
        with open(path) as f:
            data = json.load(f)
        return self._extract(data)

    def _extract(self, data: dict) -> StoragePricing:
        products = data.get("products", {})
        on_demand = data.get("terms", {}).get("OnDemand", {})

        storage_sku = put_sku = get_sku = egress_sku = None

        for sku, product in products.items():
            attrs = product.get("attributes", {})
            family = product.get("productFamily", "")
            usagetype = attrs.get("usagetype", "")

            if (
                not storage_sku
                and family == "Storage"
                and "TimedStorage-ByteHrs" in usagetype
                and attrs.get("storageClass") == "General Purpose"
            ):
                storage_sku = sku
            elif not put_sku and family == "API Request" and attrs.get("group") == "S3-API-Tier1":
                put_sku = sku
            elif not get_sku and family == "API Request" and attrs.get("group") == "S3-API-Tier2":
                get_sku = sku
            elif (
                not egress_sku
                and family == "Data Transfer"
                and attrs.get("transferType") == "AWS Outbound"
                and "DataTransfer-Out-Bytes" in usagetype
            ):
                egress_sku = sku

        def price_for(sku: str | None) -> tuple[float, str]:
            if not sku or sku not in on_demand:
                return 0.0, ""
            for term in on_demand[sku].values():
                for dim in term.get("priceDimensions", {}).values():
                    # Use first tier only (beginRange="0")
                    if dim.get("beginRange", "0") == "0":
                        p = float(dim.get("pricePerUnit", {}).get("USD", "0"))
                        if p > 0:
                            return p, dim.get("unit", "")
            return 0.0, ""

        def to_per_million(price: float, unit: str) -> float:
            u = unit.lower()
            if "1,000,000" in u or "million" in u:
                return price
            if "1,000" in u:
                return price * 1_000
            # AWS stores request prices per individual request
            return price * 1_000_000

        storage_price, _ = price_for(storage_sku)
        put_raw, put_unit = price_for(put_sku)
        get_raw, get_unit = price_for(get_sku)
        egress_price, _ = price_for(egress_sku)

        return StoragePricing(
            provider="aws_s3",
            display_name="AWS S3",
            storage_per_gb_mo=storage_price or _FALLBACK.storage_per_gb_mo,
            get_per_million=to_per_million(get_raw, get_unit) or _FALLBACK.get_per_million,
            put_per_million=to_per_million(put_raw, put_unit) or _FALLBACK.put_per_million,
            egress_per_gb=egress_price or _FALLBACK.egress_per_gb,
            free_egress_gb=100.0,
            notes="us-east-1, Standard storage class (live pricing)",
            source_url="https://aws.amazon.com/s3/pricing/",
            last_verified="live",
        )
