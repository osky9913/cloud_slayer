"""GCP Cloud Storage pricing from the authenticated Cloud Billing Catalog API."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

from ....config import force_live_prices_enabled, get_gcp_region
from ....models import StoragePricing
from ....pricing import PricingUnavailableError
from ..base import ObjectStorageProvider

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600
_GCS_SERVICE_ID = "95FF-2EF5-5EA1"
_SOURCE_URL = "https://cloud.google.com/storage/pricing"


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
        except Exception as error:
            raise PricingUnavailableError(
                self.display_name,
                f"live pricing unavailable ({error}); configure Application Default Credentials or GCP_BILLING_API_KEY",
            ) from error

    def _live_pricing(self) -> StoragePricing:
        region = get_gcp_region()
        cache_file = CACHE_DIR / f"gcp_storage_prices_{region}.json"
        if (
            not force_live_prices_enabled()
            and cache_file.exists()
            and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL
        ):
            with open(cache_file) as file:
                return _build_pricing(json.load(file), "cache")

        session = _billing_session()
        if session is None:
            raise RuntimeError("no GCP credentials for the Cloud Billing Catalog API")

        url = f"https://cloudbilling.googleapis.com/v1/services/{_GCS_SERVICE_ID}/skus"
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

        extracted = _extract_storage_prices(skus)
        missing = [
            key
            for key in ("storage_per_gb_mo", "get_per_million", "put_per_million", "egress_per_gb")
            if extracted.get(key, 0) <= 0
        ]
        if missing:
            raise ValueError(f"Billing Catalog did not contain: {', '.join(missing)}")

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as file:
            json.dump(extracted, file)
        return _build_pricing(extracted, "live")


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


def _first_tier(sku: dict) -> tuple[float, dict]:
    for info in sku.get("pricingInfo", []):
        expression = info.get("pricingExpression", {})
        tiers = expression.get("tieredRates", [])
        if tiers:
            unit = tiers[0].get("unitPrice", {})
            rate = int(unit.get("units", 0)) + int(unit.get("nanos", 0)) / 1e9
            if rate > 0:
                return rate, expression
    return 0.0, {}


def _per_million(rate: float, expression: dict) -> float:
    description = expression.get("usageUnitDescription", "").lower().replace(",", "")
    match = re.search(r"(\d+)\s*(?:count|request|operation)", description)
    units = int(match.group(1)) if match else 1
    return rate * 1_000_000 / units


def _extract_storage_prices(skus: list[dict]) -> dict[str, float]:
    region = get_gcp_region()
    result: dict[str, float] = {}
    for sku in skus:
        regions = sku.get("serviceRegions", [])
        if region not in regions and "us" not in regions:
            continue
        description = sku.get("description", "").lower()
        if any(word in description for word in ("nearline", "coldline", "archive")):
            continue
        rate, expression = _first_tier(sku)
        if rate <= 0:
            continue
        unit = expression.get("usageUnitDescription", "").lower()

        if "standard storage" in description and "gibibyte month" in unit:
            result.setdefault("storage_per_gb_mo", round(rate / 1.073741824, 6))
        elif "class a" in description and "operation" in description:
            result.setdefault("put_per_million", round(_per_million(rate, expression), 6))
        elif "class b" in description and "operation" in description:
            result.setdefault("get_per_million", round(_per_million(rate, expression), 6))
        elif (
            ("egress" in description or "data transfer out" in description)
            and "gibibyte" in unit
            and "inter-region" not in description
        ):
            result.setdefault("egress_per_gb", round(rate / 1.073741824, 6))
    return result


def _build_pricing(data: dict, source: str) -> StoragePricing:
    required = ("storage_per_gb_mo", "get_per_million", "put_per_million", "egress_per_gb")
    if any(data.get(key, 0) <= 0 for key in required):
        raise ValueError("cached GCP pricing is incomplete; clear the pricing cache and retry")
    return StoragePricing(
        provider="gcp_storage",
        display_name="GCP Cloud Storage",
        storage_per_gb_mo=data["storage_per_gb_mo"],
        get_per_million=data["get_per_million"],
        put_per_million=data["put_per_million"],
        egress_per_gb=data["egress_per_gb"],
        notes=f"{get_gcp_region()}, Standard storage class.",
        source_url=_SOURCE_URL,
        last_verified="live" if source == "live" else source,
        price_source=source,
    )
