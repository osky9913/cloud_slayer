"""Azure VM compute provider — prices fetched live from Azure Retail Prices API."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from ....config import get_azure_region
from ..base import ComputeProvider, InstanceType

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600  # 7 days

# VM specs: name → (vcpu, memory_gb)  — specs are stable; only prices are fetched live
_VM_SPECS: dict[str, tuple[int, float]] = {
    # B-series (burstable)
    "Standard_B1s": (1, 1.0),
    "Standard_B1ms": (1, 2.0),
    "Standard_B2s": (2, 4.0),
    "Standard_B4ms": (4, 16.0),
    "Standard_B8ms": (8, 32.0),
    "Standard_B12ms": (12, 48.0),
    # D-series v3
    "Standard_D2s_v3": (2, 8.0),
    "Standard_D4s_v3": (4, 16.0),
    "Standard_D8s_v3": (8, 32.0),
    "Standard_D16s_v3": (16, 64.0),
    "Standard_D32s_v3": (32, 128.0),
    # D-series v4
    "Standard_D2s_v4": (2, 8.0),
    "Standard_D4s_v4": (4, 16.0),
    "Standard_D8s_v4": (8, 32.0),
    "Standard_D16s_v4": (16, 64.0),
    # D-series AS v4 (AMD)
    "Standard_D2as_v4": (2, 8.0),
    "Standard_D4as_v4": (4, 16.0),
    "Standard_D8as_v4": (8, 32.0),
    # E-series v3 (memory optimized)
    "Standard_E2s_v3": (2, 16.0),
    "Standard_E4s_v3": (4, 32.0),
    "Standard_E8s_v3": (8, 64.0),
    "Standard_E16s_v3": (16, 128.0),
    "Standard_E32s_v3": (32, 256.0),
    # F-series v2 (compute optimized)
    "Standard_F2s_v2": (2, 4.0),
    "Standard_F4s_v2": (4, 8.0),
    "Standard_F8s_v2": (8, 16.0),
    "Standard_F16s_v2": (16, 32.0),
    "Standard_F32s_v2": (32, 64.0),
    # L-series v2 (storage optimized)
    "Standard_L8s_v2": (8, 64.0),
    "Standard_L16s_v2": (16, 128.0),
}

# Fallback prices (East US, Linux, on-demand) — verified 2026-07
_FALLBACK_PRICES: dict[str, float] = {
    # B-series
    "Standard_B1s": 7.59,
    "Standard_B1ms": 15.18,
    "Standard_B2s": 36.28,
    "Standard_B4ms": 145.12,
    "Standard_B8ms": 290.24,
    "Standard_B12ms": 435.84,
    # D-series v3
    "Standard_D2s_v3": 70.08,
    "Standard_D4s_v3": 140.16,
    "Standard_D8s_v3": 280.32,
    "Standard_D16s_v3": 560.64,
    "Standard_D32s_v3": 1121.28,
    # D-series v4
    "Standard_D2s_v4": 70.08,
    "Standard_D4s_v4": 140.16,
    "Standard_D8s_v4": 280.32,
    "Standard_D16s_v4": 560.64,
    # D-series AS v4 (AMD)
    "Standard_D2as_v4": 63.51,
    "Standard_D4as_v4": 127.01,
    "Standard_D8as_v4": 254.02,
    # E-series v3 (memory optimized)
    "Standard_E2s_v3": 100.74,
    "Standard_E4s_v3": 201.48,
    "Standard_E8s_v3": 402.96,
    "Standard_E16s_v3": 805.92,
    "Standard_E32s_v3": 1611.84,
    # F-series v2 (compute optimized)
    "Standard_F2s_v2": 61.20,
    "Standard_F4s_v2": 122.40,
    "Standard_F8s_v2": 244.80,
    "Standard_F16s_v2": 489.60,
    "Standard_F32s_v2": 979.20,
    # L-series v2 (storage optimized)
    "Standard_L8s_v2": 619.32,
    "Standard_L16s_v2": 1238.64,
}


def _notes(name: str) -> str:
    if "Standard_B" in name:
        return "Burstable, East US"
    if "Standard_D" in name and "as_v4" in name:
        return "AMD general purpose, East US"
    if "Standard_D" in name:
        return "General purpose, East US"
    if "Standard_F" in name:
        return "Compute optimized, East US"
    if "Standard_E" in name:
        return "Memory optimized, East US"
    if "Standard_L" in name:
        return "Storage optimized, East US"
    return "East US"


_CATALOG = [
    InstanceType(name, vcpu, mem, _FALLBACK_PRICES[name], _notes(name))
    for name, (vcpu, mem) in _VM_SPECS.items()
]


class AzureComputeProvider(ComputeProvider):
    @property
    def name(self) -> str:
        return "azure_vm"

    @property
    def display_name(self) -> str:
        return "Azure VM"

    def catalog(self) -> list[InstanceType]:
        try:
            return self._live_catalog()
        except Exception:
            return _CATALOG

    def _live_catalog(self) -> list[InstanceType]:
        region = get_azure_region()
        cache_file = CACHE_DIR / f"azure_compute_{region}.json"
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL:
            with open(cache_file) as f:
                return self._build_catalog(json.load(f))
        return self._fetch_and_cache(cache_file)

    def _fetch_and_cache(self, cache_file: Path) -> list[InstanceType]:
        region = get_azure_region()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        items: list[dict] = []
        url: str | None = "https://prices.azure.com/api/retail/prices"
        params: dict | None = {
            "$filter": (
                f"serviceName eq 'Virtual Machines' "
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
        return self._build_catalog(items)

    def _build_catalog(self, items: list) -> list[InstanceType]:
        size_price: dict[str, float] = {}
        for item in items:
            sku = item.get("armSkuName", "")
            if not sku or sku not in _VM_SPECS:
                continue
            if any(x in item.get("meterName", "") for x in ("Spot", "Low Priority")):
                continue
            if "Windows" in item.get("productName", ""):
                continue
            hourly = float(item.get("retailPrice", 0))
            if hourly > 0 and sku not in size_price:
                size_price[sku] = round(hourly * 730, 2)

        result = [
            InstanceType(
                name, vcpu, mem, size_price.get(name, _FALLBACK_PRICES[name]), _notes(name)
            )
            for name, (vcpu, mem) in _VM_SPECS.items()
        ]
        return result if result else _CATALOG
