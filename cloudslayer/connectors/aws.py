"""AWS Cost Explorer connector — reads real billing data and maps to cloudslayer models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..models import ComputeSpec, DatabaseSpec, ObjectStorageSpec

_HOURS_PER_MONTH = 730.0

# EC2 on-demand hourly prices (us-east-1) — used to estimate instance count from spend
_EC2_HOURLY: dict[str, float] = {
    "t3.nano": 0.0052,
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t3.xlarge": 0.1664,
    "t3.2xlarge": 0.3328,
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "m5.2xlarge": 0.384,
    "m6i.large": 0.096,
    "m6i.xlarge": 0.192,
    "m6i.2xlarge": 0.384,
    "c5.large": 0.085,
    "c5.xlarge": 0.17,
    "c5.2xlarge": 0.34,
    "r5.large": 0.126,
    "r5.xlarge": 0.252,
}

_RDS_HOURLY: dict[str, float] = {
    "db.t3.micro": 0.017,
    "db.t3.small": 0.034,
    "db.t3.medium": 0.068,
    "db.t3.large": 0.136,
    "db.t3.xlarge": 0.272,
    "db.t3.2xlarge": 0.544,
    "db.m5.large": 0.18,
    "db.m5.xlarge": 0.36,
}


@dataclass
class AWSActualResource:
    service: str  # "ec2" | "s3" | "rds"
    display_name: str  # e.g. "t3.medium × 2"
    actual_monthly_cost: float  # real $ from Cost Explorer
    current_provider: str  # cloudslayer provider name
    instance_type: str  # raw instance type string
    count: int = 1  # estimated instance count
    compute_spec: ComputeSpec | None = None
    storage_spec: ObjectStorageSpec | None = None
    database_spec: DatabaseSpec | None = None


class AWSConnector:
    """Reads AWS Cost Explorer and returns actual spend as cloudslayer-compatible resources."""

    def __init__(self, profile: str = ""):
        try:
            import boto3

            self._boto3 = boto3
        except ImportError:
            raise RuntimeError(
                "boto3 is required for AWS integration.\n"
                "Install with:  pip install 'cloudslayer[aws]'\n"
                "           or: uv add 'cloudslayer[aws]'"
            )
        self._session = boto3.Session(profile_name=profile or None)
        # Cost Explorer is always accessed via us-east-1 endpoint
        self._ce = self._session.client("ce", region_name="us-east-1")

    def validate_credentials(self) -> str:
        """Returns AWS account ID if credentials work, raises RuntimeError otherwise."""
        try:
            sts = self._session.client("sts")
            return sts.get_caller_identity()["Account"]
        except Exception as e:
            raise RuntimeError(
                f"AWS credentials not found or invalid: {e}\n\n"
                "Make sure you have AWS credentials configured:\n"
                "  • Run: aws configure\n"
                "  • Or set: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY\n"
                "  • Or use: cloudslayer actual --profile <profile-name>\n\n"
                "Required IAM permission: sts:GetCallerIdentity, ce:GetCostAndUsage"
            )

    def get_spend(self, days: int = 30) -> list[AWSActualResource]:
        end = datetime.utcnow().date()
        start = end - timedelta(days=days)
        s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

        resources: list[AWSActualResource] = []
        resources.extend(self._ec2(s, e, days))
        resources.extend(self._s3(s, e, days))
        resources.extend(self._rds(s, e, days))
        return resources

    # ── EC2 ───────────────────────────────────────────────────────────────────

    def _ec2(self, start: str, end: str, days: int) -> list[AWSActualResource]:
        try:
            resp = self._ce.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Filter={
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["Amazon Elastic Compute Cloud - Compute"],
                    }
                },
                GroupBy=[{"Type": "DIMENSION", "Key": "INSTANCE_TYPE"}],
                Metrics=["UnblendedCost"],
            )
        except Exception:
            return []

        from ..scanner import AWS_INSTANCE_SPECS

        results: list[AWSActualResource] = []
        cost_by_itype: dict[str, float] = {}

        for period in resp.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                itype = group["Keys"][0]
                if itype in ("NoInstanceType", "", "Others"):
                    continue
                cost_by_itype[itype] = cost_by_itype.get(itype, 0) + float(
                    group["Metrics"]["UnblendedCost"]["Amount"]
                )

        for itype, total_cost in cost_by_itype.items():
            if total_cost < 0.50:
                continue

            # Scale partial period to full month
            monthly = total_cost * (30 / days)

            # Estimate instance count using on-demand hourly rate
            hourly = _EC2_HOURLY.get(itype, monthly / _HOURS_PER_MONTH)
            count = max(1, round(total_cost / (hourly * days * 24)))

            vcpu, mem = AWS_INSTANCE_SPECS.get(itype, (2, 4.0))
            slug = itype.replace(".", "-")

            results.append(
                AWSActualResource(
                    service="ec2",
                    display_name=f"{itype} × {count}" if count > 1 else itype,
                    actual_monthly_cost=monthly,
                    current_provider="aws_ec2",
                    instance_type=itype,
                    count=count,
                    compute_spec=ComputeSpec(
                        name=slug,
                        vcpu=vcpu * count,
                        memory_gb=mem * count,
                    ),
                )
            )

        return sorted(results, key=lambda r: r.actual_monthly_cost, reverse=True)

    # ── S3 ────────────────────────────────────────────────────────────────────

    def _s3(self, start: str, end: str, days: int) -> list[AWSActualResource]:
        try:
            resp = self._ce.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Filter={
                    "Dimensions": {"Key": "SERVICE", "Values": ["Amazon Simple Storage Service"]}
                },
                GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
                Metrics=["UnblendedCost", "UsageQuantity"],
            )
        except Exception:
            return []

        storage_gb = 0.0
        get_requests = 0
        put_requests = 0
        egress_gb = 0.0
        total_cost = 0.0

        for period in resp.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                usage_type = group["Keys"][0]
                qty = float(group["Metrics"]["UsageQuantity"]["Amount"])
                cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                total_cost += cost

                # Usage types vary by region prefix (e.g. "USE1-TimedStorage-ByteHrs")
                ut = usage_type.split("-", 1)[-1] if "-" in usage_type else usage_type
                if "TimedStorage" in ut:
                    # GB-Hours → average GB stored (divide by hours in period)
                    storage_gb += qty / (days * 24)
                elif "Requests-Tier1" in ut:  # PUT, COPY, POST, LIST
                    put_requests += int(qty)
                elif "Requests-Tier2" in ut:  # GET, SELECT, HEAD
                    get_requests += int(qty)
                elif "DataTransfer-Out" in ut or "Bytes" in ut and "Out" in ut:
                    egress_gb += qty

        if total_cost < 0.10:
            return []

        monthly = total_cost * (30 / days)

        return [
            AWSActualResource(
                service="s3",
                display_name="S3",
                actual_monthly_cost=monthly,
                current_provider="aws_s3",
                instance_type="",
                count=1,
                storage_spec=ObjectStorageSpec(
                    name="s3",
                    storage_gb=max(1.0, round(storage_gb, 1)),
                    get_requests=get_requests,
                    put_requests=put_requests,
                    egress_gb=round(egress_gb, 1),
                ),
            )
        ]

    # ── RDS ───────────────────────────────────────────────────────────────────

    def _rds(self, start: str, end: str, days: int) -> list[AWSActualResource]:
        try:
            resp = self._ce.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Filter={
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["Amazon Relational Database Service"],
                    }
                },
                GroupBy=[{"Type": "DIMENSION", "Key": "INSTANCE_TYPE"}],
                Metrics=["UnblendedCost"],
            )
        except Exception:
            return []

        from ..scanner import AWS_RDS_SPECS

        results: list[AWSActualResource] = []
        cost_by_itype: dict[str, float] = {}

        for period in resp.get("ResultsByTime", []):
            for group in period.get("Groups", []):
                itype = group["Keys"][0]
                if itype in ("NoInstanceType", "", "Others"):
                    continue
                cost_by_itype[itype] = cost_by_itype.get(itype, 0) + float(
                    group["Metrics"]["UnblendedCost"]["Amount"]
                )

        for itype, total_cost in cost_by_itype.items():
            if total_cost < 0.50:
                continue

            monthly = total_cost * (30 / days)
            hourly = _RDS_HOURLY.get(itype, monthly / _HOURS_PER_MONTH)
            count = max(1, round(total_cost / (hourly * days * 24)))

            vcpu, mem = AWS_RDS_SPECS.get(itype, (2, 4.0))
            slug = itype.replace(".", "-")

            results.append(
                AWSActualResource(
                    service="rds",
                    display_name=f"{itype} × {count}" if count > 1 else itype,
                    actual_monthly_cost=monthly,
                    current_provider="aws_rds",
                    instance_type=itype,
                    count=count,
                    database_spec=DatabaseSpec(
                        name=slug,
                        vcpu=vcpu,
                        memory_gb=mem,
                        storage_gb=20.0,  # default; storage costs come as separate line item
                        engine="postgres",
                    ),
                )
            )

        return sorted(results, key=lambda r: r.actual_monthly_cost, reverse=True)
