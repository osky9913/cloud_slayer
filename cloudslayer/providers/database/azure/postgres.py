"""Azure Database for PostgreSQL (Flexible Server) — prices fetched live from Azure Retail Prices API."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from ....config import fallback_prices_enabled, force_live_prices_enabled, get_azure_region
from ....pricing import PricingUnavailableError
from ..base import DatabasePlan, DatabaseProvider

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600  # 7 days

# Flexible Server compute tiers: armSkuName → (vcpu, memory_gb)
_PLAN_SPECS: dict[str, tuple[int, float]] = {
    # Burstable
    "Standard_B1ms": (1, 2.0),
    "Standard_B2s": (2, 4.0),
    "Standard_B2ms": (2, 8.0),
    "Standard_B4ms": (4, 16.0),
    # General Purpose (Dsv3)
    "Standard_D2s_v3": (2, 8.0),
    "Standard_D4s_v3": (4, 16.0),
    "Standard_D8s_v3": (8, 32.0),
    "Standard_D16s_v3": (16, 64.0),
    # Memory Optimized (Esv3)
    "Standard_E2s_v3": (2, 16.0),
    "Standard_E4s_v3": (4, 32.0),
    "Standard_E8s_v3": (8, 64.0),
}

_STORAGE_PER_GB = 0.115  # Flexible Server storage, East US — stable

# Fallback prices (East US, single server, on-demand) — verified 2026-07
_FALLBACK_PRICES: dict[str, float] = {
    "Standard_B1ms": 12.41,
    "Standard_B2s": 24.82,
    "Standard_B2ms": 49.64,
    "Standard_B4ms": 99.28,
    "Standard_D2s_v3": 124.83,
    "Standard_D4s_v3": 249.66,
    "Standard_D8s_v3": 499.32,
    "Standard_D16s_v3": 998.64,
    "Standard_E2s_v3": 183.96,
    "Standard_E4s_v3": 367.92,
    "Standard_E8s_v3": 735.84,
}


def _notes(name: str) -> str:
    if "Standard_B" in name:
        return "Burstable, Flexible Server, East US"
    if "Standard_D" in name:
        return "General purpose, Flexible Server, East US"
    if "Standard_E" in name:
        return "Memory optimized, Flexible Server, East US"
    return "Flexible Server, East US"


_PLANS = [
    DatabasePlan(
        name,
        vcpu,
        mem,
        _FALLBACK_PRICES[name],
        _STORAGE_PER_GB,
        0.0,
        _notes(name),
        "fallback",
        "https://azure.microsoft.com/pricing/details/postgresql/flexible-server/",
    )
    for name, (vcpu, mem) in _PLAN_SPECS.items()
]


class AzurePostgresProvider(DatabaseProvider):
    @property
    def name(self) -> str:
        return "azure_db"

    @property
    def display_name(self) -> str:
        return "Azure DB for PostgreSQL"

    def plans(self) -> list[DatabasePlan]:
        try:
            return self._live_plans()
        except Exception as error:
            if fallback_prices_enabled():
                return _PLANS
            raise PricingUnavailableError(
                self.display_name,
                f"live pricing unavailable ({error}); rerun with --fallback to use verified static Azure prices",
            ) from error

    def _live_plans(self) -> list[DatabasePlan]:
        region = get_azure_region()
        cache_file = CACHE_DIR / f"azure_postgres_{region}.json"
        if (
            not force_live_prices_enabled()
            and cache_file.exists()
            and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL
        ):
            with open(cache_file) as f:
                return _build_plans(json.load(f), "cache")
        return self._fetch_and_cache(cache_file)

    def _fetch_and_cache(self, cache_file: Path) -> list[DatabasePlan]:
        region = get_azure_region()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        items: list[dict] = []
        url: str | None = "https://prices.azure.com/api/retail/prices"
        params: dict | None = {
            "$filter": (
                f"serviceName eq 'Azure Database for PostgreSQL' "
                f"and armRegionName eq '{region}' "
                "and priceType eq 'Consumption'"
            )
        }
        while url:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            items.extend(body.get("Items", []))
            url = body.get("NextPageLink")
            params = None
        with open(cache_file, "w") as f:
            json.dump(items, f)
        return _build_plans(items, "live")


def _build_plans(items: list, source: str = "live") -> list[DatabasePlan]:
    sku_price: dict[str, float] = {}
    storage_price = 0.0
    for item in items:
        meter = item.get("meterName", "").lower()
        unit = item.get("unitOfMeasure", "").lower()
        if "storage" in meter and "gb/month" in unit and not storage_price:
            storage_price = float(item.get("retailPrice", 0))
            continue
        sku = item.get("armSkuName", "")
        if not sku or sku not in _PLAN_SPECS:
            continue
        if "Flexible Server" not in item.get("productName", ""):
            continue
        # Skip high-availability replica meters — price the single-server baseline
        if "HA" in item.get("meterName", "") or "HA" in item.get("skuName", ""):
            continue
        hourly = float(item.get("retailPrice", 0))
        if hourly > 0 and sku not in sku_price:
            sku_price[sku] = round(hourly * 730, 2)

    storage_is_live = storage_price > 0
    if not storage_is_live and not fallback_prices_enabled():
        raise ValueError("Azure Retail Prices API did not contain PostgreSQL storage pricing")
    storage_price = storage_price or _STORAGE_PER_GB
    plans = [
        DatabasePlan(
            name,
            vcpu,
            mem,
            sku_price.get(name, _FALLBACK_PRICES[name]),
            storage_price,
            0.0,
            _notes(name),
            source if name in sku_price and storage_is_live else "mixed fallback",
            "https://azure.microsoft.com/pricing/details/postgresql/flexible-server/",
        )
        for name, (vcpu, mem) in _PLAN_SPECS.items()
        if name in sku_price or fallback_prices_enabled()
    ]
    if plans:
        return plans
    if fallback_prices_enabled():
        return _PLANS
    raise ValueError("Azure Retail Prices API returned no supported PostgreSQL plans")
