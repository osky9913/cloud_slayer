from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cloudslayer.analyzer import load_from_terraform
from cloudslayer.cli import _configure_pricing, app
from cloudslayer.config import force_live_prices_enabled, set_fallback_prices
from cloudslayer.models import ComputeSpec
from cloudslayer.pricing import PricingUnavailableError
from cloudslayer.providers.compute.aws.ec2 import AWSEC2Provider
from cloudslayer.providers.compute.gcp.gce import GCPComputeProvider, _build_catalog


def test_aws_fallback_is_rejected_by_default(monkeypatch):
    set_fallback_prices(False)
    monkeypatch.setattr(
        AWSEC2Provider, "_live_catalog", lambda _self: (_ for _ in ()).throw(OSError("offline"))
    )
    with pytest.raises(PricingUnavailableError, match="--fallback"):
        AWSEC2Provider().catalog()


def test_aws_fallback_is_explicit_and_marked(monkeypatch):
    set_fallback_prices(True)
    monkeypatch.setattr(
        AWSEC2Provider, "_live_catalog", lambda _self: (_ for _ in ()).throw(OSError("offline"))
    )
    result = AWSEC2Provider().calculate_cost(ComputeSpec("api", 2, 4), "t3.medium")
    assert result is not None
    assert result.price_per_month == 30.37
    assert result.price_source == "fallback"


def test_gcp_never_uses_hardcoded_fallback(monkeypatch):
    set_fallback_prices(True)
    monkeypatch.setattr(
        GCPComputeProvider, "_live_catalog", lambda _self: (_ for _ in ()).throw(OSError("offline"))
    )
    with pytest.raises(PricingUnavailableError, match="GCP_BILLING_API_KEY"):
        GCPComputeProvider().catalog()


def test_gcp_catalog_contains_only_returned_live_skus():
    catalog = _build_catalog({"e2-medium": 31.25}, "live")
    assert [(item.name, item.price_per_month, item.price_source) for item in catalog] == [
        ("e2-medium", 31.25, "live")
    ]


def test_terraform_analysis_prices_exact_current_instance(monkeypatch):
    set_fallback_prices(True)
    resources = load_from_terraform("examples/01-startup-aws")
    app = next(resource for resource in resources if resource.name == "app")
    worker = next(resource for resource in resources if resource.name == "worker")
    assert app.instance_type == "t3.medium"
    assert app.monthly_cost == 30.37
    assert worker.instance_type == "t3.large"
    assert worker.monthly_cost == 60.74


def test_fallback_flag_produces_valid_json_with_provenance():
    result = CliRunner().invoke(
        app,
        ["plan", "examples/basic.hcl", "--fallback", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    sources = {
        item["price_source"]
        for resource_group in payload.values()
        for resource in resource_group
        for item in resource["results"]
    }
    assert "fallback" in sources
    assert "live" in sources


def test_configure_pricing_live_flag_controls_cache_mode():
    _configure_pricing(fallback=False, live=True)
    assert force_live_prices_enabled() is True

    _configure_pricing(fallback=False, live=False)
    assert force_live_prices_enabled() is False


def test_live_flag_is_accepted_by_plan_command():
    result = CliRunner().invoke(
        app,
        ["plan", "examples/basic.hcl", "--live", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "compute" in payload
