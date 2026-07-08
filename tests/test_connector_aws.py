"""Tests for the AWS Cost Explorer connector — uses moto to mock AWS APIs."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Set fake AWS credentials before importing anything that touches boto3
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

boto3 = pytest.importorskip("boto3", reason="boto3 not installed")
moto = pytest.importorskip("moto", reason="moto not installed")

from moto import mock_aws  # noqa: E402  (moto >= 5 unified decorator)

from cloudslayer.connectors.aws import AWSConnector  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ce_response(groups: list[dict]) -> dict:
    """Build a minimal Cost Explorer get_cost_and_usage response."""
    return {
        "ResultsByTime": [{
            "TimePeriod": {"Start": "2026-06-01", "End": "2026-07-01"},
            "Groups": groups,
            "Estimated": False,
        }],
        "ResponseMetadata": {},
    }


def _group(key: str, cost: str, qty: str = "730") -> dict:
    return {
        "Keys": [key],
        "Metrics": {
            "UnblendedCost": {"Amount": cost, "Unit": "USD"},
            "UsageQuantity": {"Amount": qty, "Unit": "Hrs"},
        },
    }


# ── AWSConnector unit tests (mocked CE client) ─────────────────────────────

class TestAWSConnectorEC2:
    def setup_method(self):
        self.connector = AWSConnector.__new__(AWSConnector)
        self.connector._boto3 = boto3
        self.connector._session = MagicMock()
        self.connector._ce = MagicMock()

    def test_ec2_single_instance(self):
        self.connector._ce.get_cost_and_usage.return_value = _make_ce_response([
            _group("t3.medium", "30.37", "730"),
        ])
        results = self.connector._ec2("2026-06-01", "2026-07-01", 30)
        assert len(results) == 1
        r = results[0]
        assert r.service == "ec2"
        assert r.instance_type == "t3.medium"
        assert r.count == 1
        assert r.actual_monthly_cost == pytest.approx(30.37, abs=0.10)
        assert r.compute_spec is not None
        assert r.compute_spec.vcpu == 2
        assert r.compute_spec.memory_gb == 4.0

    def test_ec2_multiple_instances_estimated_from_cost(self):
        # $60.74 on t3.medium ≈ 2 instances ($30.37 each)
        self.connector._ce.get_cost_and_usage.return_value = _make_ce_response([
            _group("t3.medium", "60.74", "1460"),
        ])
        results = self.connector._ec2("2026-06-01", "2026-07-01", 30)
        assert results[0].count == 2
        assert results[0].compute_spec.vcpu == 4   # 2 vCPU × 2 instances
        assert results[0].compute_spec.memory_gb == 8.0

    def test_ec2_skips_trivial_charges(self):
        self.connector._ce.get_cost_and_usage.return_value = _make_ce_response([
            _group("t3.micro", "0.10"),   # under $0.50 threshold
            _group("t3.medium", "30.37"),
        ])
        results = self.connector._ec2("2026-06-01", "2026-07-01", 30)
        assert len(results) == 1
        assert results[0].instance_type == "t3.medium"

    def test_ec2_skips_noinstancetype(self):
        self.connector._ce.get_cost_and_usage.return_value = _make_ce_response([
            _group("NoInstanceType", "5.00"),
            _group("t3.medium", "30.37"),
        ])
        results = self.connector._ec2("2026-06-01", "2026-07-01", 30)
        assert all(r.instance_type != "NoInstanceType" for r in results)

    def test_ec2_multiple_instance_types(self):
        self.connector._ce.get_cost_and_usage.return_value = _make_ce_response([
            _group("t3.medium", "30.37"),
            _group("t3.xlarge", "121.47"),
        ])
        results = self.connector._ec2("2026-06-01", "2026-07-01", 30)
        assert len(results) == 2
        # Sorted by cost descending
        assert results[0].instance_type == "t3.xlarge"
        assert results[1].instance_type == "t3.medium"

    def test_ec2_api_error_returns_empty(self):
        self.connector._ce.get_cost_and_usage.side_effect = Exception("AccessDenied")
        results = self.connector._ec2("2026-06-01", "2026-07-01", 30)
        assert results == []

    def test_ec2_provider_is_aws_ec2(self):
        self.connector._ce.get_cost_and_usage.return_value = _make_ce_response([
            _group("t3.medium", "30.37"),
        ])
        results = self.connector._ec2("2026-06-01", "2026-07-01", 30)
        assert results[0].current_provider == "aws_ec2"

    def test_ec2_scales_partial_period_to_month(self):
        # 7 days of spend should be scaled × (30/7) to get monthly estimate
        weekly_spend = 30.37 / 4.28  # ≈ $7.09 for 7 days
        self.connector._ce.get_cost_and_usage.return_value = _make_ce_response([
            _group("t3.medium", str(weekly_spend)),
        ])
        results = self.connector._ec2("2026-06-24", "2026-07-01", 7)
        assert results[0].actual_monthly_cost == pytest.approx(30.37, abs=2.0)


class TestAWSConnectorS3:
    def setup_method(self):
        self.connector = AWSConnector.__new__(AWSConnector)
        self.connector._boto3 = boto3
        self.connector._session = MagicMock()
        self.connector._ce = MagicMock()

    def _s3_response(self, usage_types: list[tuple[str, str, str]]) -> dict:
        """usage_types: list of (usage_type, cost, quantity)"""
        return _make_ce_response([
            {
                "Keys": [ut],
                "Metrics": {
                    "UnblendedCost": {"Amount": cost, "Unit": "USD"},
                    "UsageQuantity": {"Amount": qty, "Unit": "GB-Mo"},
                },
            }
            for ut, cost, qty in usage_types
        ])

    def test_s3_basic(self):
        # 500 GB stored for 30 days = 500 × 24 × 30 GB-Hours
        gb_hours = str(500 * 24 * 30)
        self.connector._ce.get_cost_and_usage.return_value = self._s3_response([
            ("USE1-TimedStorage-ByteHrs", "11.50", gb_hours),
        ])
        results = self.connector._s3("2026-06-01", "2026-07-01", 30)
        assert len(results) == 1
        r = results[0]
        assert r.service == "s3"
        assert r.current_provider == "aws_s3"
        assert r.storage_spec is not None
        assert r.storage_spec.storage_gb == pytest.approx(500.0, abs=1.0)

    def test_s3_skips_trivial_charges(self):
        self.connector._ce.get_cost_and_usage.return_value = self._s3_response([
            ("TimedStorage-ByteHrs", "0.05", "100"),
        ])
        results = self.connector._s3("2026-06-01", "2026-07-01", 30)
        assert results == []

    def test_s3_api_error_returns_empty(self):
        self.connector._ce.get_cost_and_usage.side_effect = Exception("AccessDenied")
        results = self.connector._s3("2026-06-01", "2026-07-01", 30)
        assert results == []

    def test_s3_parses_egress(self):
        self.connector._ce.get_cost_and_usage.return_value = self._s3_response([
            ("USE1-TimedStorage-ByteHrs", "5.00", "100000"),
            ("USE1-DataTransfer-Out-Bytes", "2.70", "30"),   # 30 GB egress
        ])
        results = self.connector._s3("2026-06-01", "2026-07-01", 30)
        assert results[0].storage_spec.egress_gb == pytest.approx(30.0, abs=1.0)


class TestAWSConnectorRDS:
    def setup_method(self):
        self.connector = AWSConnector.__new__(AWSConnector)
        self.connector._boto3 = boto3
        self.connector._session = MagicMock()
        self.connector._ce = MagicMock()

    def test_rds_basic(self):
        self.connector._ce.get_cost_and_usage.return_value = _make_ce_response([
            _group("db.t3.medium", "49.64"),
        ])
        results = self.connector._rds("2026-06-01", "2026-07-01", 30)
        assert len(results) == 1
        r = results[0]
        assert r.service == "rds"
        assert r.current_provider == "aws_rds"
        assert r.database_spec is not None
        assert r.database_spec.vcpu == 2
        assert r.database_spec.memory_gb == 4.0

    def test_rds_api_error_returns_empty(self):
        self.connector._ce.get_cost_and_usage.side_effect = Exception("AccessDenied")
        results = self.connector._rds("2026-06-01", "2026-07-01", 30)
        assert results == []


# ── validate_credentials ──────────────────────────────────────────────────────

def test_validate_credentials_bad_creds():
    connector = AWSConnector.__new__(AWSConnector)
    connector._boto3 = boto3
    mock_session = MagicMock()
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.side_effect = Exception("InvalidClientTokenId")
    mock_session.client.return_value = mock_sts
    connector._session = mock_session

    with pytest.raises(RuntimeError, match="AWS credentials not found"):
        connector.validate_credentials()


# ── import guard ──────────────────────────────────────────────────────────────

def test_missing_boto3_raises_helpful_error(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("No module named 'boto3'")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(builtins, "__import__", mock_import)
        with pytest.raises(RuntimeError, match="boto3 is required"):
            AWSConnector()
