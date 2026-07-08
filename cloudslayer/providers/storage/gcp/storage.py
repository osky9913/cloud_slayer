"""GCP Cloud Storage provider — live prices via Cloud Billing Catalog API (ADC or API key).

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

from ....models import StoragePricing
from ..base import ObjectStorageProvider

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600

_GCS_SERVICE_ID = "95FF-2EF5-5EA1"

_FALLBACK = StoragePricing(
    provider="gcp_storage",
    display_name="GCP Cloud Storage",
    storage_per_gb_mo=0.020,
    get_per_million=0.40,
    put_per_million=5.00,
    egress_per_gb=0.12,
    free_storage_gb=0.0,
    free_egress_gb=1.0,
    notes="us-east1, Standard storage class.",
    source_url="https://cloud.google.com/storage/pricing",
    last_verified="2026-07-03",
)


class GCPStorageProvider(ObjectStorageProvider):
    @property
    def name(self) -> str:
        return "gcp_storage"

    @property
    def display_name(self) -> str:
        return "GCP Cloud Storage"

    def get_pricing(self) -> StoragePricing:
        try:
            return self._live_pricing()
        except Exception:
            return _FALLBACK

    def _live_pricing(self) -> StoragePricing:
        cache_file = CACHE_DIR / "gcp_storage_prices.json"
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL:
            with open(cache_file) as f:
                return _build_pricing(json.load(f))

        session = _billing_session()
        if session is None:
            raise RuntimeError("no GCP credentials for live pricing")

        url = f"https://cloudbilling.googleapis.com/v1/services/{_GCS_SERVICE_ID}/skus"
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

        extracted = _extract_storage_prices(skus)
        if not extracted:
            raise ValueError("no GCS prices found in Billing Catalog")

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(extracted, f)
        return _build_pricing(extracted)


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


def _extract_rate(sku: dict, unit: str = "gibibyte month") -> float:
    for info in sku.get("pricingInfo", []):
        expr = info.get("pricingExpression", {})
        if unit not in expr.get("usageUnitDescription", "").lower():
            continue
        tiers = expr.get("tieredRates", [])
        if tiers:
            u = tiers[0].get("unitPrice", {})
            return int(u.get("units", 0)) + int(u.get("nanos", 0)) / 1e9
    return 0.0


def _extract_storage_prices(skus: list[dict]) -> dict:
    storage_per_gb = 0.0
    egress_per_gb = 0.0

    for sku in skus:
        desc = sku.get("description", "")
        regions = sku.get("serviceRegions", [])
        if "us-east1" not in regions and "us" not in regions:
            continue
        desc_lower = desc.lower()
        if "standard storage" in desc_lower and not storage_per_gb:
            r = _extract_rate(sku, "gibibyte month")
            if r > 0:
                storage_per_gb = round(r / 1.073741824, 4)  # GiB → GB
        elif "egress" in desc_lower and "na" in desc_lower and not egress_per_gb:
            r = _extract_rate(sku, "gibibyte")
            if r > 0:
                egress_per_gb = round(r / 1.073741824, 4)

    if not storage_per_gb:
        return {}
    return {
        "storage_per_gb_mo": storage_per_gb,
        "egress_per_gb": egress_per_gb or _FALLBACK.egress_per_gb,
    }


def _build_pricing(data: dict) -> StoragePricing:
    return StoragePricing(
        provider="gcp_storage",
        display_name="GCP Cloud Storage",
        storage_per_gb_mo=data.get("storage_per_gb_mo", _FALLBACK.storage_per_gb_mo),
        get_per_million=_FALLBACK.get_per_million,
        put_per_million=_FALLBACK.put_per_million,
        egress_per_gb=data.get("egress_per_gb", _FALLBACK.egress_per_gb),
        free_storage_gb=_FALLBACK.free_storage_gb,
        free_egress_gb=_FALLBACK.free_egress_gb,
        notes="us-east1, Standard storage class (live pricing).",
        source_url="https://cloud.google.com/storage/pricing",
        last_verified="live",
    )
