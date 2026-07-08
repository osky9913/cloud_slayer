from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from ....config import get_azure_region
from ....models import StoragePricing
from ..base import ObjectStorageProvider

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600

# Azure Retail Prices API — public, no auth required
AZURE_PRICES_URL = "https://prices.azure.com/api/retail/prices"

_FALLBACK = StoragePricing(
    provider="azure_blob",
    display_name="Azure Blob (Hot)",
    storage_per_gb_mo=0.018,
    get_per_million=0.40,
    put_per_million=5.00,
    egress_per_gb=0.087,
    free_storage_gb=0.0,
    free_egress_gb=5.0,
    notes="Hot tier, LRS, East US — fallback values (2026-07-03)",
    source_url="https://azure.microsoft.com/en-us/pricing/details/storage/blobs/",
    last_verified="2026-07-03",
)


class AzureBlobProvider(ObjectStorageProvider):
    @property
    def name(self) -> str:
        return "azure_blob"

    @property
    def display_name(self) -> str:
        return "Azure Blob (Hot)"

    def get_pricing(self) -> StoragePricing:
        try:
            return self._load_or_fetch()
        except Exception:
            return _FALLBACK

    def _cache_path(self) -> Path:
        region = get_azure_region()
        return CACHE_DIR / f"azure_blob_{region}.json"

    def _load_or_fetch(self) -> StoragePricing:
        cache_file = self._cache_path()
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL:
            with open(cache_file) as f:
                return self._extract(json.load(f))
        return self._fetch_and_cache(cache_file)

    def _fetch_and_cache(self, cache_file: Path) -> StoragePricing:
        region = get_azure_region()
        azure_filter = (
            f"serviceName eq 'Storage' "
            f"and armRegionName eq '{region}' "
            "and skuName eq 'Hot LRS' "
            "and priceType eq 'Consumption'"
        )
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(
            AZURE_PRICES_URL,
            params={"$filter": azure_filter},
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("Items", [])
        with open(cache_file, "w") as f:
            json.dump(items, f)
        return self._extract(items)

    def _extract(self, items: list) -> StoragePricing:
        storage_price = _FALLBACK.storage_per_gb_mo
        write_price = _FALLBACK.put_per_million
        read_price = _FALLBACK.get_per_million

        for item in items:
            meter = item.get("meterName", "")
            price = float(item.get("retailPrice", 0))
            if not price:
                continue

            if meter == "LRS Data Stored":
                storage_price = price  # per GB/Month
            elif meter == "Write Operations":
                write_price = price * 100  # per 10K → per million
            elif meter == "Read Operations":
                read_price = price * 100

        return StoragePricing(
            provider="azure_blob",
            display_name="Azure Blob (Hot)",
            storage_per_gb_mo=storage_price,
            get_per_million=read_price,
            put_per_million=write_price,
            egress_per_gb=0.087,  # Egress is under Azure Bandwidth service
            free_storage_gb=0.0,
            free_egress_gb=5.0,
            notes="Hot tier, LRS, East US. Egress via Azure Bandwidth pricing.",
            source_url="https://azure.microsoft.com/en-us/pricing/details/storage/blobs/",
            last_verified="live",
        )
