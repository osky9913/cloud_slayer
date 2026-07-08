"""Tests for the strategy engine."""
from __future__ import annotations

import pytest

from cloudslayer.models import ComputeSpec, ObjectStorageSpec, DatabaseSpec
from cloudslayer.strategies import (
    AnalysisResource,
    Strategy,
    run_all_strategies,
    _strategy_region_shift,
    _strategy_graviton,
    _strategy_aws_reserved as _strategy_reserved,
    _strategy_aws_spot as _strategy_spot,
    _AWS_RI_DISCOUNTS,
)

_RI_DISCOUNT_1YR = _AWS_RI_DISCOUNTS[1]
_RI_DISCOUNT_3YR = _AWS_RI_DISCOUNTS[3]


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _compute(name="web", itype="t3.medium", cost=30.37, provider="aws_ec2"):
    return AnalysisResource(
        name=name,
        service="compute",
        current_provider=provider,
        monthly_cost=cost,
        compute_spec=ComputeSpec(name=name, vcpu=2, memory_gb=4.0),
        instance_type=itype,
    )


def _storage(name="assets", cost=11.50, provider="aws_s3"):
    return AnalysisResource(
        name=name,
        service="storage",
        current_provider=provider,
        monthly_cost=cost,
        storage_spec=ObjectStorageSpec(name=name, storage_gb=100),
    )


def _database(name="db", itype="db.t3.medium", cost=49.64, provider="aws_rds"):
    return AnalysisResource(
        name=name,
        service="database",
        current_provider=provider,
        monthly_cost=cost,
        database_spec=DatabaseSpec(name=name, vcpu=2, memory_gb=4.0, storage_gb=20),
        instance_type=itype,
    )


# ── Region shift ──────────────────────────────────────────────────────────────

class TestRegionShift:
    def test_saves_on_aws_resources(self):
        s = _strategy_region_shift([_compute(cost=100.0), _database(cost=50.0)])
        assert s is not None
        assert s.savings_mo > 0
        assert s.effort == "Low"
        assert s.risk == "Low"

    def test_skips_non_aws(self):
        r = _compute(provider="gcp_gce")
        assert _strategy_region_shift([r]) is None

    def test_one_item_per_resource(self):
        s = _strategy_region_shift([_compute(cost=100.0), _database(cost=50.0)])
        assert len(s.items) == 2

    def test_to_cost_is_lower(self):
        s = _strategy_region_shift([_compute(cost=100.0)])
        assert all(item.to_cost < item.from_cost for item in s.items)

    def test_trivial_savings_returns_none(self):
        # Very small cost → savings < $2 threshold
        s = _strategy_region_shift([_compute(cost=1.0)])
        assert s is None


# ── Graviton ──────────────────────────────────────────────────────────────────

class TestGraviton:
    def test_ec2_t3_mapped_to_t4g(self):
        s = _strategy_graviton([_compute(itype="t3.medium", cost=30.37)])
        assert s is not None
        assert s.savings_mo == pytest.approx(30.37 * 0.20, abs=0.01)
        assert "t4g.medium" in s.items[0].to_label

    def test_rds_arm_mapped(self):
        s = _strategy_graviton([_database(itype="db.t3.medium", cost=49.64)])
        assert s is not None
        assert s.savings_mo == pytest.approx(49.64 * 0.20, abs=0.01)
        assert "db.t4g.medium" in s.items[0].to_label

    def test_unmapped_type_returns_none(self):
        assert _strategy_graviton([_compute(itype="m7i.large", cost=100.0)]) is None

    def test_both_ec2_and_rds_summed(self):
        s = _strategy_graviton([
            _compute(itype="t3.medium", cost=30.0),
            _database(itype="db.t3.medium", cost=50.0),
        ])
        assert s is not None
        assert len(s.items) == 2
        assert s.savings_mo == pytest.approx(30.0 * 0.20 + 50.0 * 0.20, abs=0.01)


# ── Reserved instances ────────────────────────────────────────────────────────

class TestReserved:
    def test_1yr_saves_30pct(self):
        s = _strategy_reserved([_compute(cost=100.0)], 1)
        assert s is not None
        assert s.savings_mo == pytest.approx(30.0, abs=0.01)
        assert s.effort == "None"
        assert s.risk == "Low"

    def test_3yr_saves_50pct(self):
        s = _strategy_reserved([_compute(cost=100.0)], 3)
        assert s is not None
        assert s.savings_mo == pytest.approx(50.0, abs=0.01)
        assert s.risk == "Medium"

    def test_storage_not_eligible(self):
        assert _strategy_reserved([_storage(cost=100.0)], 1) is None

    def test_compute_and_database_both_eligible(self):
        s = _strategy_reserved([_compute(cost=100.0), _database(cost=50.0)], 1)
        assert s is not None
        assert len(s.items) == 2
        assert s.savings_mo == pytest.approx(150.0 * 0.30, abs=0.01)


# ── Spot ──────────────────────────────────────────────────────────────────────

class TestSpot:
    def test_t3_family_discounted(self):
        s = _strategy_spot([_compute(itype="t3.medium", cost=30.37)])
        assert s is not None
        assert s.savings_mo > 0
        assert s.risk == "Medium"

    def test_unknown_family_returns_none(self):
        assert _strategy_spot([_compute(itype="x2iezn.large", cost=100.0)]) is None

    def test_database_not_included(self):
        s = _strategy_spot([_database(cost=100.0)])
        assert s is None

    def test_to_cost_lower_than_from(self):
        s = _strategy_spot([_compute(itype="m5.large", cost=70.08)])
        assert s is not None
        assert all(item.to_cost < item.from_cost for item in s.items)


# ── run_all_strategies ────────────────────────────────────────────────────────

class TestRunAllStrategies:
    def test_returns_list(self):
        resources = [_compute(cost=100.0), _database(cost=50.0), _storage(cost=20.0)]
        strategies = run_all_strategies(resources)
        assert isinstance(strategies, list)
        assert len(strategies) > 0

    def test_sorted_by_priority_then_savings(self):
        resources = [_compute(cost=200.0), _database(cost=100.0), _storage(cost=20.0)]
        strategies = run_all_strategies(resources)
        for i in range(len(strategies) - 1):
            a, b = strategies[i], strategies[i + 1]
            assert (a.priority, -a.savings_mo) <= (b.priority, -b.savings_mo)

    def test_all_have_required_fields(self):
        strategies = run_all_strategies([_compute(cost=100.0)])
        for s in strategies:
            assert s.id
            assert s.name
            assert s.pitch
            assert s.savings_mo > 0
            assert s.effort in ("None", "Low", "Medium", "High")
            assert s.risk in ("Low", "Medium", "High")

    def test_empty_resources_returns_empty(self):
        assert run_all_strategies([]) == []

    def test_gcp_resources_skip_aws_strategies(self):
        r = _compute(provider="gcp_gce", itype="e2-medium", cost=25.0)
        strategies = run_all_strategies([r])
        ids = {s.id for s in strategies}
        assert "region_shift" not in ids
        assert "graviton" not in ids
