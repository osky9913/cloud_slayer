from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from ....config import fallback_prices_enabled, force_live_prices_enabled, get_azure_region
from ....models import StoragePricing
from ....pricing import PricingUnavailableError
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
    price_source="fallback",
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
        except Exception as error:
            if fallback_prices_enabled():
                return _FALLBACK
            raise PricingUnavailableError(
                self.display_name,
                f"live pricing unavailable ({error}); rerun with --fallback to use verified static Azure prices",
            ) from error

    def _cache_path(self) -> Path:
        region = get_azure_region()
        return CACHE_DIR / f"azure_blob_{region}.json"

    def _load_or_fetch(self) -> StoragePricing:
        cache_file = self._cache_path()
        if (
            not force_live_prices_enabled()
            and cache_file.exists()
            and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL
        ):
            with open(cache_file) as f:
                cached = json.load(f)
            if isinstance(cached, dict) and "storage" in cached:
                return self._extract(cached, "cache")
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
        storage_resp = requests.get(
            AZURE_PRICES_URL,
            params={"$filter": azure_filter},
            timeout=30,
        )
        storage_resp.raise_for_status()
        bandwidth_filter = (
            f"serviceName eq 'Bandwidth' and armRegionName eq '{region}' "
            "and priceType eq 'Consumption'"
        )
        bandwidth_resp = requests.get(
            AZURE_PRICES_URL,
            params={"$filter": bandwidth_filter},
            timeout=30,
        )
        bandwidth_resp.raise_for_status()
        payload = {
            "storage": storage_resp.json().get("Items", []),
            "bandwidth": bandwidth_resp.json().get("Items", []),
        }
        with open(cache_file, "w") as f:
            json.dump(payload, f)
        return self._extract(payload, "live")

    def _extract(self, payload: dict, source: str = "live") -> StoragePricing:
        storage_price = write_price = read_price = egress_price = 0.0

        for item in payload.get("storage", []):
            meter = item.get("meterName", "")
            price = float(item.get("retailPrice", 0))
            if (
                not price
                or item.get("productName") != "Blob Storage"
                or not item.get("isPrimaryMeterRegion", False)
                or float(item.get("tierMinimumUnits", 0)) != 0
            ):
                continue

            if "Data Stored" in meter:
                storage_price = price  # per GB/Month
            elif "Write Operations" in meter:
                write_price = price * 100  # per 10K → per million
            elif "Read Operations" in meter:
                read_price = price * 100

        for item in payload.get("bandwidth", []):
            meter = item.get("meterName", "").lower()
            price = float(item.get("retailPrice", 0))
            if price > 0 and "data transfer out" in meter:
                egress_price = price
                break

        values = {
            "storage": storage_price,
            "get": read_price,
            "put": write_price,
            "egress": egress_price,
        }
        missing = [name for name, value in values.items() if value <= 0]
        if missing and not fallback_prices_enabled():
            raise ValueError(f"Azure Retail Prices API did not contain: {', '.join(missing)}")
        price_source = "mixed fallback" if missing else source

        return StoragePricing(
            provider="azure_blob",
            display_name="Azure Blob (Hot)",
            storage_per_gb_mo=storage_price or _FALLBACK.storage_per_gb_mo,
            get_per_million=read_price or _FALLBACK.get_per_million,
            put_per_million=write_price or _FALLBACK.put_per_million,
            egress_per_gb=egress_price or _FALLBACK.egress_per_gb,
            free_storage_gb=0.0,
            free_egress_gb=5.0,
            notes="Hot tier, LRS, East US. Egress via Azure Bandwidth pricing.",
            source_url="https://azure.microsoft.com/en-us/pricing/details/storage/blobs/",
            last_verified="live",
            price_source=price_source,
        )
