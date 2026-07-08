"""GCP Cloud SQL provider — live prices via Cloud Billing Catalog API (ADC or API key).

Live pricing requires one of:
  • Application Default Credentials:  gcloud auth application-default login
  • Environment variable:             GCP_BILLING_API_KEY=AIza...

Falls back to hardcoded verified prices when neither is available.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from ..base import DatabasePlan, DatabaseProvider

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600

_CLOUDSQL_SERVICE_ID = "9662-B51E-5089"

_STORAGE_PER_GB = 0.17  # SSD persistent disk, us-east1
_INCLUDED_STORAGE_GB = 10.0

_PLAN_SPECS: dict[str, tuple[int, float]] = {
    "db-g1-small": (1, 1.7),
    "db-n1-standard-1": (1, 3.75),
    "db-n1-standard-2": (2, 7.5),
    "db-n1-standard-4": (4, 15.0),
    "db-n1-standard-8": (8, 30.0),
    "db-n1-highmem-2": (2, 13.0),
    "db-n1-highmem-4": (4, 26.0),
}

# Fractional vCPU for shared-core db-g1-small
_EFFECTIVE_VCPU: dict[str, float] = {
    "db-g1-small": 0.5,
}

_FALLBACK_PRICES: dict[str, float] = {
    "db-g1-small": 25.14,
    "db-n1-standard-1": 46.57,
    "db-n1-standard-2": 93.14,
    "db-n1-standard-4": 186.28,
    "db-n1-standard-8": 372.57,
    "db-n1-highmem-2": 124.19,
    "db-n1-highmem-4": 248.37,
}


def _notes(name: str) -> str:
    if "highmem" in name:
        return "High memory, PostgreSQL"
    if name == "db-g1-small":
        return "Shared vCPU, PostgreSQL"
    return "Standard, PostgreSQL"


_PLANS = [
    DatabasePlan(
        name, vcpu, mem, _FALLBACK_PRICES[name], _STORAGE_PER_GB, _INCLUDED_STORAGE_GB, _notes(name)
    )
    for name, (vcpu, mem) in _PLAN_SPECS.items()
]


class GCPCloudSQLProvider(DatabaseProvider):
    @property
    def name(self) -> str:
        return "gcp_cloudsql"

    @property
    def display_name(self) -> str:
        return "GCP Cloud SQL"

    def plans(self) -> list[DatabasePlan]:
        try:
            return self._live_plans()
        except Exception:
            return _PLANS

    def _live_plans(self) -> list[DatabasePlan]:
        cache_file = CACHE_DIR / "gcp_cloudsql_prices.json"
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL:
            with open(cache_file) as f:
                return _build_plans(json.load(f))
        try:
            prices = _fetch_billing_api()
        except Exception:
            prices = None

        if prices:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w") as f:
                json.dump(prices, f)
            return _build_plans(prices)

        if cache_file.exists():
            with open(cache_file) as f:
                return _build_plans(json.load(f))

        raise RuntimeError("no live GCP Cloud SQL prices available")


def _billing_session():
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"]
        )
        return google.auth.transport.requests.AuthorizedSession(credentials)
    except Exception:
        pass

    api_key = os.environ.get("GCP_BILLING_API_KEY", "").strip()
    if api_key:

        class _KeySession:
            def get(self, url: str, **kwargs) -> requests.Response:
                params = kwargs.pop("params", {})
                params["key"] = api_key
                return requests.get(url, params=params, **kwargs)

        return _KeySession()

    return None


def _fetch_billing_api() -> dict[str, float] | None:
    session = _billing_session()
    if session is None:
        return None

    url = f"https://cloudbilling.googleapis.com/v1/services/{_CLOUDSQL_SERVICE_ID}/skus"
    skus: list[dict] = []
    page_token = None
    while True:
        params: dict = {"pageSize": 500, "currencyCode": "USD"}
        if page_token:
            params["pageToken"] = page_token
        resp = session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        skus.extend(body.get("skus", []))
        page_token = body.get("nextPageToken")
        if not page_token:
            break

    return _calculate_prices(skus) or None


def _extract_hourly_rate(sku: dict) -> float:
    for info in sku.get("pricingInfo", []):
        expr = info.get("pricingExpression", {})
        if expr.get("usageUnit") != "h":
            continue
        tiers = expr.get("tieredRates", [])
        if tiers:
            u = tiers[0].get("unitPrice", {})
            return int(u.get("units", 0)) + int(u.get("nanos", 0)) / 1e9
    return 0.0


def _calculate_prices(skus: list[dict]) -> dict[str, float]:
    """Extract per-vCPU and per-RAM rates for Cloud SQL N1 in us-east1."""
    cpu_rate = 0.0
    ram_rate = 0.0

    for sku in skus:
        desc = sku.get("description", "")
        if "us-east1" not in sku.get("serviceRegions", []):
            continue
        if sku.get("category", {}).get("usageType") != "OnDemand":
            continue
        if "PostgreSQL" not in desc and "MySQL" not in desc and "SQL Server" not in desc:
            continue
        hourly = _extract_hourly_rate(sku)
        if hourly <= 0:
            continue
        desc_lower = desc.lower()
        if "core" in desc_lower and "n1" in desc_lower and not cpu_rate:
            cpu_rate = hourly
        elif "ram" in desc_lower and "n1" in desc_lower and not ram_rate:
            ram_rate = hourly

    if not cpu_rate or not ram_rate:
        return {}

    prices: dict[str, float] = {}
    for name, (vcpu, mem) in _PLAN_SPECS.items():
        eff_vcpu = _EFFECTIVE_VCPU.get(name, float(vcpu))
        prices[name] = round((eff_vcpu * cpu_rate + mem * ram_rate) * 730, 2)
    return prices


def _build_plans(prices: dict[str, float]) -> list[DatabasePlan]:
    return [
        DatabasePlan(
            name,
            vcpu,
            mem,
            prices.get(name, _FALLBACK_PRICES[name]),
            _STORAGE_PER_GB,
            _INCLUDED_STORAGE_GB,
            _notes(name),
        )
        for name, (vcpu, mem) in _PLAN_SPECS.items()
    ]
