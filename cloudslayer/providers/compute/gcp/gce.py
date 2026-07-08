"""GCP Compute Engine provider — live prices via Cloud Billing Catalog API (ADC or API key).

Live pricing requires one of:
  • Application Default Credentials:  gcloud auth application-default login
  • Environment variable:             GCP_BILLING_API_KEY=AIza...

Uses instances.vantage.sh for supported dedicated-vCPU types when the authenticated
Cloud Billing Catalog is unavailable. It never uses hard-coded prices.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from ....config import force_live_prices_enabled, get_gcp_region
from ....pricing import PricingUnavailableError
from ..base import ComputeProvider, InstanceType

CACHE_DIR = Path.home() / ".cloudslayer" / "cache"
CACHE_TTL = 7 * 24 * 3600  # 7 days

# GCP Compute Engine service ID in the Cloud Billing Catalog
_GCE_SERVICE_ID = "6F81-5844-456A"

# Instance specs: name → (vcpu, memory_gb)
_INSTANCE_SPECS: dict[str, tuple[int, float]] = {
    # e2 shared-core
    "e2-micro": (2, 1.0),
    "e2-small": (2, 2.0),
    "e2-medium": (2, 4.0),
    # e2 standard
    "e2-standard-2": (2, 8.0),
    "e2-standard-4": (4, 16.0),
    "e2-standard-8": (8, 32.0),
    "e2-standard-16": (16, 64.0),
    "e2-standard-32": (32, 128.0),
    # n1 standard
    "n1-standard-1": (1, 3.75),
    "n1-standard-2": (2, 7.5),
    "n1-standard-4": (4, 15.0),
    "n1-standard-8": (8, 30.0),
    # n1 highcpu / highmem
    "n1-highcpu-4": (4, 3.6),
    "n1-highcpu-8": (8, 7.2),
    "n1-highmem-2": (2, 13.0),
    "n1-highmem-4": (4, 26.0),
    "n1-highmem-8": (8, 52.0),
    # n2 standard
    "n2-standard-2": (2, 8.0),
    "n2-standard-4": (4, 16.0),
    "n2-standard-8": (8, 32.0),
    "n2-standard-16": (16, 64.0),
    # n2 highmem
    "n2-highmem-2": (2, 16.0),
    "n2-highmem-4": (4, 32.0),
    "n2-highmem-8": (8, 64.0),
    "n2-highmem-16": (16, 128.0),
    # c2
    "c2-standard-4": (4, 16.0),
    "c2-standard-8": (8, 32.0),
    "c2-standard-16": (16, 64.0),
    # n2d (AMD EPYC)
    "n2d-standard-2": (2, 8.0),
    "n2d-standard-4": (4, 16.0),
    "n2d-standard-8": (8, 32.0),
    # t2d (AMD Tau, burstable)
    "t2d-standard-1": (1, 4.0),
    "t2d-standard-2": (2, 8.0),
    "t2d-standard-4": (4, 16.0),
}

# Effective vCPU allocation for GCP billing (shared-core instances use fractional vCPUs)
_EFFECTIVE_VCPU: dict[str, float] = {
    "e2-micro": 0.25,
    "e2-small": 0.50,
    "e2-medium": 1.00,
}

# Machine series → SKU description substrings to match in the Billing Catalog
# Format: (cpu_description_fragment, ram_description_fragment)
_SERIES_SKU_HINTS: dict[str, tuple[str, str]] = {
    "e2": ("E2 Instance Core", "E2 Instance Ram"),
    "n1": ("N1 Predefined Instance Core", "N1 Predefined Instance Ram"),
    "n2": ("N2 Instance Core", "N2 Instance Ram"),
    "n2d": ("N2D AMD Instance Core", "N2D AMD Instance Ram"),
    "c2": ("Compute optimized Core", "Compute optimized Ram"),
    "t2d": ("T2D AMD Instance Core", "T2D AMD Instance Ram"),
}


def _notes(name: str) -> str:
    if name in ("e2-micro", "e2-small", "e2-medium"):
        return "Shared vCPU"
    if name.startswith("c2"):
        return "Compute optimized"
    if "highmem" in name:
        return "High memory"
    if name.startswith("n1"):
        return "Standard (N1)"
    if name.startswith("n2d"):
        return "Standard, AMD EPYC"
    if name.startswith("t2d"):
        return "Burstable, AMD Tau"
    return "Standard"


class GCPComputeProvider(ComputeProvider):
    @property
    def name(self) -> str:
        return "gcp_gce"

    @property
    def display_name(self) -> str:
        return "GCP Compute Engine"

    def catalog(self) -> list[InstanceType]:
        try:
            return self._live_catalog()
        except Exception as error:
            raise PricingUnavailableError(
                self.display_name,
                f"live pricing unavailable ({error}); configure Application Default Credentials or GCP_BILLING_API_KEY",
            ) from error

    def _live_catalog(self) -> list[InstanceType]:
        region = get_gcp_region()
        cache_file = CACHE_DIR / f"gcp_compute_prices_{region}.json"
        if (
            not force_live_prices_enabled()
            and cache_file.exists()
            and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL
        ):
            with open(cache_file) as f:
                return _build_catalog(json.load(f), "cache")
        try:
            return self._fetch_and_cache(cache_file)
        except Exception:
            raise

    def _fetch_and_cache(self, cache_file: Path) -> list[InstanceType]:
        prices = _fetch_billing_api()
        source = "live"
        if not prices:
            prices = _fetch_vantage()
            source = "third-party live"
        if not prices:
            raise ValueError("no GCP compute prices available from any source")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump({"prices": prices, "source": source}, f)
        return _build_catalog(prices, source)


# ── Cloud Billing Catalog API ──────────────────────────────────────────────────


def _fetch_billing_api() -> dict[str, float] | None:
    """Try to fetch rates from the Cloud Billing Catalog API (ADC or API key)."""
    session = _billing_session()
    if session is None:
        return None

    skus = _paginate_skus(session)
    if not skus:
        return None

    return _calculate_prices(skus)


def _billing_session():
    """Return an authenticated session (ADC preferred, then API key, then None)."""
    # Try Application Default Credentials first
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"]
        )
        return google.auth.transport.requests.AuthorizedSession(credentials)
    except Exception:
        pass

    # Try GCP_BILLING_API_KEY
    api_key = os.environ.get("GCP_BILLING_API_KEY", "").strip()
    if api_key:

        class _KeySession:
            def get(self, url: str, **kwargs) -> requests.Response:
                params = kwargs.pop("params", {})
                params["key"] = api_key
                return requests.get(url, params=params, **kwargs)

        return _KeySession()

    return None


def _paginate_skus(session) -> list[dict]:
    """Fetch all Compute Engine SKUs from the Cloud Billing Catalog."""
    url = f"https://cloudbilling.googleapis.com/v1/services/{_GCE_SERVICE_ID}/skus"
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
    return skus


def _calculate_prices(skus: list[dict]) -> dict[str, float]:
    """Calculate monthly prices per instance type from per-vCPU and per-RAM rates."""
    gcp_region = get_gcp_region()
    # Find per-vCPU and per-RAM hourly rates for each machine series in the target region
    series_rates: dict[str, dict[str, float]] = {}  # series → {"cpu": rate, "ram": rate}

    for sku in skus:
        desc = sku.get("description", "")
        if "Preemptible" in desc or "Spot" in desc or "Commitment" in desc:
            continue
        if gcp_region not in sku.get("serviceRegions", []):
            continue
        category = sku.get("category", {})
        if category.get("usageType") != "OnDemand":
            continue

        hourly = _extract_hourly_rate(sku)
        if hourly <= 0:
            continue

        for series, (cpu_hint, ram_hint) in _SERIES_SKU_HINTS.items():
            if series not in series_rates:
                series_rates[series] = {}
            if cpu_hint in desc and "cpu" not in series_rates[series]:
                series_rates[series]["cpu"] = hourly
            elif ram_hint in desc and "ram" not in series_rates[series]:
                series_rates[series]["ram"] = hourly

    if not series_rates:
        return {}

    prices: dict[str, float] = {}
    for name, (vcpu, mem) in _INSTANCE_SPECS.items():
        series = name.split("-")[0]
        rates = series_rates.get(series)
        if not rates or "cpu" not in rates or "ram" not in rates:
            continue
        eff_vcpu = _EFFECTIVE_VCPU.get(name, float(vcpu))
        monthly = (eff_vcpu * rates["cpu"] + mem * rates["ram"]) * 730
        prices[name] = round(monthly, 2)

    return prices


def _extract_hourly_rate(sku: dict) -> float:
    """Extract the first-tier hourly USD rate from a SKU."""
    for info in sku.get("pricingInfo", []):
        expr = info.get("pricingExpression", {})
        if expr.get("usageUnit") != "h":
            continue
        tiers = expr.get("tieredRates", [])
        if tiers:
            unit_price = tiers[0].get("unitPrice", {})
            nanos = int(unit_price.get("nanos", 0))
            units = int(unit_price.get("units", 0))
            return units + nanos / 1e9
    return 0.0


# ── Vantage fallback (dedicated-vCPU only) ────────────────────────────────────

_VANTAGE_SKIP = {"e2-micro", "e2-small", "e2-medium"}  # shared-core: Vantage data is wrong

# Types where Vantage coverage is inconsistent; require the Cloud Billing API.
_VANTAGE_UNSUPPORTED = frozenset(
    name
    for name in _INSTANCE_SPECS
    if (name.startswith("n1") or name.startswith("t2d") or name.startswith("n2d"))
)


def _fetch_vantage() -> dict[str, float] | None:
    """Fetch from instances.vantage.sh — accurate for dedicated-vCPU types only."""
    try:
        gcp_region = get_gcp_region()
        resp = requests.get("https://instances.vantage.sh/gcp/instances.json", timeout=60)
        resp.raise_for_status()
        prices: dict[str, float] = {}
        for item in resp.json():
            name = item.get("instance_type", "")
            if name not in _INSTANCE_SPECS or name in _VANTAGE_SKIP or name in _VANTAGE_UNSUPPORTED:
                continue
            if item.get("shared_cpu"):
                continue
            p = item.get("pricing", {}).get(gcp_region, {}).get("linux", {})
            hourly = float(p.get("ondemand", 0))
            if hourly > 0:
                prices[name] = round(hourly * 730, 2)
        return prices if prices else None
    except Exception:
        return None


def _build_catalog(data: dict, source: str = "live") -> list[InstanceType]:
    prices = data.get("prices", data)
    source = data.get("source", source)
    return [
        InstanceType(
            name,
            vcpu,
            mem,
            prices[name],
            _notes(name),
            source,
            "https://cloud.google.com/compute/vm-instance-pricing",
        )
        for name, (vcpu, mem) in _INSTANCE_SPECS.items()
        if name in prices
    ]
