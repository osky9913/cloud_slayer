"""GCP Cloud SQL pricing from the authenticated Cloud Billing Catalog API."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from ....config import force_live_prices_enabled, get_gcp_region
from ....pricing import PricingUnavailableError
from ..base import DatabasePlan, DatabaseProvider

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600
_CLOUDSQL_SERVICE_ID = "9662-B51E-5089"
_SOURCE_URL = "https://cloud.google.com/sql/pricing"

_PLAN_SPECS: dict[str, tuple[int, float]] = {
    "db-g1-small": (1, 1.7),
    "db-n1-standard-1": (1, 3.75),
    "db-n1-standard-2": (2, 7.5),
    "db-n1-standard-4": (4, 15.0),
    "db-n1-standard-8": (8, 30.0),
    "db-n1-highmem-2": (2, 13.0),
    "db-n1-highmem-4": (4, 26.0),
}
_EFFECTIVE_VCPU = {"db-g1-small": 0.5}


def _notes(name: str) -> str:
    if "highmem" in name:
        return "High memory, PostgreSQL"
    if name == "db-g1-small":
        return "Shared vCPU, PostgreSQL"
    return "Standard, PostgreSQL"


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
        except Exception as error:
            raise PricingUnavailableError(
                self.display_name,
                f"live pricing unavailable ({error}); configure Application Default Credentials or GCP_BILLING_API_KEY",
            ) from error

    def _live_plans(self) -> list[DatabasePlan]:
        region = get_gcp_region()
        cache_file = CACHE_DIR / f"gcp_cloudsql_prices_{region}.json"
        if (
            not force_live_prices_enabled()
            and cache_file.exists()
            and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL
        ):
            with open(cache_file) as file:
                return _build_plans(json.load(file), "cache")

        pricing = _fetch_billing_api()
        if not pricing:
            raise RuntimeError("no GCP credentials for the Cloud Billing Catalog API")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as file:
            json.dump(pricing, file)
        return _build_plans(pricing, "live")


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


def _fetch_billing_api() -> dict | None:
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
        response = session.get(url, params=params, timeout=30)
        response.raise_for_status()
        body = response.json()
        skus.extend(body.get("skus", []))
        page_token = body.get("nextPageToken")
        if not page_token:
            break
    return _calculate_pricing(skus) or None


def _first_rate(sku: dict) -> tuple[float, str]:
    for info in sku.get("pricingInfo", []):
        expression = info.get("pricingExpression", {})
        tiers = expression.get("tieredRates", [])
        if tiers:
            unit = tiers[0].get("unitPrice", {})
            rate = int(unit.get("units", 0)) + int(unit.get("nanos", 0)) / 1e9
            return rate, expression.get("usageUnitDescription", "").lower()
    return 0.0, ""


def _calculate_pricing(skus: list[dict]) -> dict:
    region = get_gcp_region()
    cpu_rate = ram_rate = storage_rate = 0.0
    for sku in skus:
        description = sku.get("description", "").lower()
        if region not in sku.get("serviceRegions", []):
            continue
        if sku.get("category", {}).get("usageType") != "OnDemand":
            continue
        if "postgres" not in description:
            continue
        rate, unit = _first_rate(sku)
        if rate <= 0:
            continue
        if "core" in description and "n1" in description and "hour" in unit and not cpu_rate:
            cpu_rate = rate
        elif "ram" in description and "n1" in description and "hour" in unit and not ram_rate:
            ram_rate = rate
        elif (
            "ssd" in description
            and "storage" in description
            and "gibibyte month" in unit
            and not storage_rate
        ):
            storage_rate = rate / 1.073741824

    if not cpu_rate or not ram_rate or not storage_rate:
        return {}
    prices = {
        name: round(
            (_EFFECTIVE_VCPU.get(name, float(vcpu)) * cpu_rate + memory * ram_rate) * 730, 2
        )
        for name, (vcpu, memory) in _PLAN_SPECS.items()
    }
    return {"prices": prices, "storage_per_gb": round(storage_rate, 6)}


def _build_plans(data: dict, source: str) -> list[DatabasePlan]:
    prices = data.get("prices", {})
    storage = float(data.get("storage_per_gb", 0))
    if not prices or storage <= 0:
        raise ValueError(
            "cached GCP Cloud SQL pricing is incomplete; clear the pricing cache and retry"
        )
    return [
        DatabasePlan(
            name,
            vcpu,
            memory,
            prices[name],
            storage,
            0.0,
            _notes(name),
            source,
            _SOURCE_URL,
        )
        for name, (vcpu, memory) in _PLAN_SPECS.items()
        if name in prices
    ]
