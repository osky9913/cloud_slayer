"""Strategy engine — generates cost-saving recommendations from a unified resource list."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import ComputeSpec, DatabaseSpec, ObjectStorageSpec

# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class AnalysisResource:
    name: str
    service: str  # "compute" | "storage" | "database"
    current_provider: str  # "aws_ec2" | "aws_s3" | "aws_rds" etc.
    monthly_cost: float
    compute_spec: ComputeSpec | None = None
    storage_spec: ObjectStorageSpec | None = None
    database_spec: DatabaseSpec | None = None
    region: str = "us-east-1"
    instance_type: str = ""


@dataclass
class StrategyItem:
    resource_name: str
    from_label: str
    to_label: str
    from_cost: float
    to_cost: float
    note: str = ""


@dataclass
class Strategy:
    id: str
    name: str
    pitch: str
    savings_mo: float
    savings_pct: float
    effort: str  # "None" | "Low" | "Medium" | "High"
    risk: str  # "Low" | "Medium" | "High"
    items: list[StrategyItem] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    overhead_mo: float = 0.0
    migration_cost_est: float = 0.0  # one-time engineer-hours cost ($)
    break_even_months: float = 0.0  # migration_cost_est / savings_mo
    is_dominant: bool = False  # beats all alternatives on every dimension
    priority: int = 2  # 1=immediate wins, 2=commitment purchase, 3=major migration


# ── Pricing constants ─────────────────────────────────────────────────────────

_AWS_REGION_ALTS: list[tuple[str, str, float]] = [
    ("eu-central-1", "Frankfurt", 0.95),
    ("eu-west-1", "Ireland", 0.95),
    ("eu-north-1", "Stockholm", 0.95),
    ("ap-south-1", "Mumbai", 0.93),
]

_AWS_RI_DISCOUNTS = {1: 0.30, 3: 0.50}
_GCP_CUD_DISCOUNTS = {1: 0.20, 3: 0.37}  # Committed Use Discounts
_AZURE_RI_DISCOUNTS = {1: 0.28, 3: 0.45}  # Azure Reserved VM Instances
_AWS_SAVINGS_PLANS_DISCOUNTS = {1: 0.33, 3: 0.50}  # Compute Savings Plans (cross-family flexible)

_COLD_TIER_LABEL: dict[str, str] = {
    "aws_s3": "Intelligent-Tiering",
    "gcp_storage": "Nearline/Coldline",
    "azure_blob": "Cool/Archive tier",
}

_SCHEDULER_UPTIME_FACTOR = 0.30  # 10h/day weekdays = 50h/168h ≈ 30% active → 70% savings
_LIFECYCLE_SAVINGS_FACTOR = 0.28  # ~40% cold data × 70% tier discount ≈ 28% total bill reduction

_EC2_ARM_MAP: dict[str, tuple[str, float]] = {
    "t3.nano": ("t4g.nano", 0.20),
    "t3.micro": ("t4g.micro", 0.20),
    "t3.small": ("t4g.small", 0.20),
    "t3.medium": ("t4g.medium", 0.20),
    "t3.large": ("t4g.large", 0.20),
    "t3.xlarge": ("t4g.xlarge", 0.20),
    "t3.2xlarge": ("t4g.2xlarge", 0.20),
    "m5.large": ("m6g.large", 0.20),
    "m5.xlarge": ("m6g.xlarge", 0.20),
    "m5.2xlarge": ("m6g.2xlarge", 0.20),
    "m5.4xlarge": ("m6g.4xlarge", 0.20),
    "m6i.large": ("m6g.large", 0.10),
    "m6i.xlarge": ("m6g.xlarge", 0.10),
    "m6i.2xlarge": ("m6g.2xlarge", 0.10),
    "m6i.4xlarge": ("m6g.4xlarge", 0.10),
    "c5.large": ("c6g.large", 0.20),
    "c5.xlarge": ("c6g.xlarge", 0.20),
    "c5.2xlarge": ("c6g.2xlarge", 0.20),
    "c5.4xlarge": ("c6g.4xlarge", 0.20),
    "c6i.large": ("c6g.large", 0.10),
    "c6i.xlarge": ("c6g.xlarge", 0.10),
    "c6i.2xlarge": ("c6g.2xlarge", 0.10),
    "c6i.4xlarge": ("c6g.4xlarge", 0.10),
    "r5.large": ("r6g.large", 0.20),
    "r5.xlarge": ("r6g.xlarge", 0.20),
    "r5.2xlarge": ("r6g.2xlarge", 0.20),
    "r6i.large": ("r6g.large", 0.10),
    "r6i.xlarge": ("r6g.xlarge", 0.10),
    "r6i.2xlarge": ("r6g.2xlarge", 0.10),
}

_RDS_ARM_MAP: dict[str, tuple[str, float]] = {
    "db.t3.micro": ("db.t4g.micro", 0.20),
    "db.t3.small": ("db.t4g.small", 0.20),
    "db.t3.medium": ("db.t4g.medium", 0.20),
    "db.t3.large": ("db.t4g.large", 0.20),
    "db.t3.xlarge": ("db.t4g.xlarge", 0.20),
    "db.t3.2xlarge": ("db.t4g.2xlarge", 0.20),
    "db.m5.large": ("db.m6g.large", 0.20),
    "db.m5.xlarge": ("db.m6g.xlarge", 0.20),
    "db.m5.2xlarge": ("db.m6g.2xlarge", 0.20),
    "db.m6i.large": ("db.m6g.large", 0.10),
    "db.m6i.xlarge": ("db.m6g.xlarge", 0.10),
    "db.m6i.2xlarge": ("db.m6g.2xlarge", 0.10),
    "db.r5.large": ("db.r6g.large", 0.20),
    "db.r5.xlarge": ("db.r6g.xlarge", 0.20),
    "db.r5.2xlarge": ("db.r6g.2xlarge", 0.20),
    "db.r6i.large": ("db.r6g.large", 0.10),
    "db.r6i.xlarge": ("db.r6g.xlarge", 0.10),
    "db.r6i.2xlarge": ("db.r6g.2xlarge", 0.10),
}

_AWS_SPOT_DISCOUNTS: dict[str, float] = {
    "t3": 0.70,
    "t4g": 0.60,
    "m5": 0.72,
    "m6i": 0.72,
    "m6g": 0.65,
    "c5": 0.70,
    "c6i": 0.70,
    "c6g": 0.65,
    "r5": 0.65,
    "r6i": 0.65,
    "r6g": 0.60,
    "i3": 0.70,
    "g4dn": 0.60,
}

_GCP_PREEMPTIBLE_DISCOUNTS: dict[str, float] = {
    "e2": 0.70,
    "n1": 0.80,
    "n2": 0.60,
    "n2d": 0.60,
    "c2": 0.60,
    "t2d": 0.60,
}

_AWS_SIZE_ORDER = [
    "nano",
    "micro",
    "small",
    "medium",
    "large",
    "xlarge",
    "2xlarge",
    "4xlarge",
    "8xlarge",
    "16xlarge",
    "32xlarge",
]

# Engineer-hours estimates for one-time migration cost (at $150/h blended rate)
_MIGRATION_COSTS = {
    "region_shift": 4 * 150,  # update Terraform region + test
    "graviton": 4 * 150,  # test arm64 builds + update launch config
    "aws_reserved": 0,  # just a purchase
    "gcp_cud": 0,
    "azure_ri": 0,
    "aws_spot": 2 * 150,  # update ASG launch template
    "gcp_preemptible": 2 * 150,
    "rightsize": 2 * 150,  # config change + test + deploy
    "full_migration": 160 * 150,  # full re-deployment + testing
    "portfolio": 0,
    "aws_savings_plans": 0,
    "instance_scheduler": 4 * 150,  # EventBridge rule + smoke test
    "storage_lifecycle": 2 * 150,  # lifecycle rules in Terraform
}


def _break_even(migration_cost: float, savings_mo: float) -> float:
    if savings_mo <= 0:
        return 0.0
    return round(migration_cost / savings_mo, 1)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _downsize_aws(instance_type: str) -> str | None:
    """t3.2xlarge → t3.xlarge, etc. Returns None if already small or unknown."""
    if "." not in instance_type:
        return None
    family, size = instance_type.rsplit(".", 1)
    if size not in _AWS_SIZE_ORDER:
        return None
    idx = _AWS_SIZE_ORDER.index(size)
    if idx < 3:  # don't suggest below medium
        return None
    return f"{family}.{_AWS_SIZE_ORDER[idx - 1]}"


def _downsize_gcp(instance_type: str) -> str | None:
    """n2-standard-8 → n2-standard-4. Returns None if already small."""
    m = re.match(r"^(.+-\D+)(\d+)$", instance_type)
    if not m:
        return None
    prefix, num_str = m.group(1), m.group(2)
    vcpus = int(num_str)
    if vcpus <= 2:
        return None
    return f"{prefix}{vcpus // 2}"


def _downsize_azure(instance_type: str) -> str | None:
    """Standard_D4s_v3 → Standard_D2s_v3. Returns None if already small."""
    m = re.match(r"^(Standard_[A-Za-z]+)(\d+)(.*)$", instance_type)
    if not m:
        return None
    prefix, num_str, suffix = m.group(1), m.group(2), m.group(3)
    n = int(num_str)
    if n <= 2:
        return None
    return f"{prefix}{n // 2}{suffix}"


# ── Strategy generators ───────────────────────────────────────────────────────


def _strategy_region_shift(resources: list[AnalysisResource]) -> Strategy | None:
    from .config import get_aws_region

    aws = [r for r in resources if r.current_provider.startswith("aws_")]
    if not aws:
        return None

    current_region = get_aws_region()
    current_total = sum(r.monthly_cost for r in aws)
    best_region, best_city, best_mult = min(_AWS_REGION_ALTS, key=lambda x: x[2])
    savings_total = current_total * (1 - best_mult)
    if savings_total < 2.0:
        return None

    return Strategy(
        id="region_shift",
        name=f"Region shift → {best_region} ({best_city})",
        pitch="Same provider, same services. Update the region in your Terraform provider block.",
        savings_mo=round(savings_total, 2),
        savings_pct=round(savings_total / current_total * 100, 1),
        effort="Low",
        risk="Low",
        items=[
            StrategyItem(
                resource_name=r.name,
                from_label=f"{current_region}  ${r.monthly_cost:.2f}/mo",
                to_label=f"{best_region}  ${r.monthly_cost * best_mult:.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * best_mult,
            )
            for r in aws
        ],
        caveats=[
            "EU regions may trigger GDPR compliance requirements.",
            "Run latency benchmarks after the move — especially for user-facing APIs.",
        ],
        migration_cost_est=_MIGRATION_COSTS["region_shift"],
        break_even_months=_break_even(_MIGRATION_COSTS["region_shift"], round(savings_total, 2)),
        priority=1,
    )


def _strategy_graviton(resources: list[AnalysisResource]) -> Strategy | None:
    items: list[StrategyItem] = []
    total_savings = 0.0
    total_current = 0.0

    for r in resources:
        if r.service == "compute" and r.instance_type in _EC2_ARM_MAP:
            arm_type, discount = _EC2_ARM_MAP[r.instance_type]
            saving = r.monthly_cost * discount
            items.append(
                StrategyItem(
                    resource_name=r.name,
                    from_label=f"{r.instance_type}  ${r.monthly_cost:.2f}/mo",
                    to_label=f"{arm_type}  ${r.monthly_cost * (1 - discount):.2f}/mo",
                    from_cost=r.monthly_cost,
                    to_cost=r.monthly_cost * (1 - discount),
                    note=f"−{int(discount * 100)}% Graviton",
                )
            )
            total_savings += saving
            total_current += r.monthly_cost
        elif r.service == "database" and r.instance_type in _RDS_ARM_MAP:
            arm_type, discount = _RDS_ARM_MAP[r.instance_type]
            saving = r.monthly_cost * discount
            items.append(
                StrategyItem(
                    resource_name=r.name,
                    from_label=f"{r.instance_type}  ${r.monthly_cost:.2f}/mo",
                    to_label=f"{arm_type}  ${r.monthly_cost * (1 - discount):.2f}/mo",
                    from_cost=r.monthly_cost,
                    to_cost=r.monthly_cost * (1 - discount),
                    note=f"−{int(discount * 100)}% Graviton (RDS)",
                )
            )
            total_savings += saving
            total_current += r.monthly_cost

    if not items or total_savings < 2.0:
        return None

    return Strategy(
        id="graviton",
        name="Graviton / ARM (same provider)",
        pitch="Drop-in ARM replacement. Change the instance type and redeploy — no migration.",
        savings_mo=round(total_savings, 2),
        savings_pct=round(total_savings / total_current * 100, 1),
        effort="Low",
        risk="Low",
        items=items,
        caveats=[
            "Verify your app builds and runs on arm64 — most modern software does.",
            "Docker images must include the linux/arm64 platform layer.",
        ],
        migration_cost_est=_MIGRATION_COSTS["graviton"],
        break_even_months=_break_even(_MIGRATION_COSTS["graviton"], round(total_savings, 2)),
        priority=1,
    )


def _strategy_aws_reserved(resources: list[AnalysisResource], years: int) -> Strategy | None:
    discount = _AWS_RI_DISCOUNTS[years]
    eligible = [
        r
        for r in resources
        if r.service in ("compute", "database") and r.current_provider.startswith("aws_")
    ]
    if not eligible:
        return None
    current_total = sum(r.monthly_cost for r in eligible)
    savings = current_total * discount
    if savings < 5.0:
        return None

    return Strategy(
        id=f"aws_reserved_{years}yr",
        name=f"AWS {years}-year Reserved Instances",
        pitch=f"No migration. Commit to {years} year(s) and save ~{int(discount * 100)}% on EC2 and RDS.",
        savings_mo=round(savings, 2),
        savings_pct=round(discount * 100, 1),
        effort="None",
        risk="Low" if years == 1 else "Medium",
        items=[
            StrategyItem(
                resource_name=r.name,
                from_label=f"on-demand  ${r.monthly_cost:.2f}/mo",
                to_label=f"reserved {years}yr  ${r.monthly_cost * (1 - discount):.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * (1 - discount),
            )
            for r in eligible
        ],
        caveats=[
            f"Commitment is {years} year(s) — costly to exit early.",
            "Unused RIs can be sold on the AWS Marketplace.",
            "Actual RI pricing varies by instance family and payment option.",
        ],
        migration_cost_est=_MIGRATION_COSTS["aws_reserved"],
        break_even_months=_break_even(_MIGRATION_COSTS["aws_reserved"], round(savings, 2)),
        priority=2,
    )


def _strategy_gcp_cud(resources: list[AnalysisResource], years: int) -> Strategy | None:
    discount = _GCP_CUD_DISCOUNTS[years]
    eligible = [
        r
        for r in resources
        if r.service in ("compute", "database") and r.current_provider.startswith("gcp_")
    ]
    if not eligible:
        return None
    current_total = sum(r.monthly_cost for r in eligible)
    savings = current_total * discount
    if savings < 5.0:
        return None

    return Strategy(
        id=f"gcp_cud_{years}yr",
        name=f"GCP {years}-year Committed Use Discounts",
        pitch=f"Commit to {years} year(s) of GCP usage and save ~{int(discount * 100)}% on Compute Engine and Cloud SQL.",
        savings_mo=round(savings, 2),
        savings_pct=round(discount * 100, 1),
        effort="None",
        risk="Low" if years == 1 else "Medium",
        items=[
            StrategyItem(
                resource_name=r.name,
                from_label=f"on-demand  ${r.monthly_cost:.2f}/mo",
                to_label=f"CUD {years}yr  ${r.monthly_cost * (1 - discount):.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * (1 - discount),
            )
            for r in eligible
        ],
        caveats=[
            f"CUD commitment is {years} year(s) — billed monthly even if unused.",
            "CUDs apply per region and per machine family (n1, n2, etc.).",
            "Cloud SQL CUDs cover instance cost only, not storage.",
        ],
        migration_cost_est=_MIGRATION_COSTS["gcp_cud"],
        break_even_months=_break_even(_MIGRATION_COSTS["gcp_cud"], round(savings, 2)),
        priority=2,
    )


def _strategy_azure_ri(resources: list[AnalysisResource], years: int) -> Strategy | None:
    discount = _AZURE_RI_DISCOUNTS[years]
    eligible = [
        r
        for r in resources
        if r.service in ("compute", "database") and r.current_provider.startswith("azure_")
    ]
    if not eligible:
        return None
    current_total = sum(r.monthly_cost for r in eligible)
    savings = current_total * discount
    if savings < 5.0:
        return None

    return Strategy(
        id=f"azure_ri_{years}yr",
        name=f"Azure {years}-year Reserved VM Instances",
        pitch=f"Commit to {years} year(s) and save ~{int(discount * 100)}% on Azure VMs and databases.",
        savings_mo=round(savings, 2),
        savings_pct=round(discount * 100, 1),
        effort="None",
        risk="Low" if years == 1 else "Medium",
        items=[
            StrategyItem(
                resource_name=r.name,
                from_label=f"pay-as-you-go  ${r.monthly_cost:.2f}/mo",
                to_label=f"reserved {years}yr  ${r.monthly_cost * (1 - discount):.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * (1 - discount),
            )
            for r in eligible
        ],
        caveats=[
            f"Reservation is {years} year(s) — cancellation incurs a 12% fee.",
            "Reserved capacity is scoped to a region and VM size series.",
            "Can be exchanged or cancelled via the Azure portal.",
        ],
        migration_cost_est=_MIGRATION_COSTS["azure_ri"],
        break_even_months=_break_even(_MIGRATION_COSTS["azure_ri"], round(savings, 2)),
        priority=2,
    )


def _strategy_aws_savings_plans(resources: list[AnalysisResource], years: int) -> Strategy | None:
    """AWS Compute Savings Plans — cross-family commitment, works after Graviton migrations."""
    discount = _AWS_SAVINGS_PLANS_DISCOUNTS[years]
    eligible = [
        r
        for r in resources
        if r.service in ("compute", "database") and r.current_provider.startswith("aws_")
    ]
    if not eligible:
        return None
    current_total = sum(r.monthly_cost for r in eligible)
    savings = current_total * discount
    if savings < 5.0:
        return None

    return Strategy(
        id=f"aws_savings_plans_{years}yr",
        name=f"AWS {years}-year Compute Savings Plans",
        pitch=(
            "Like Reserved Instances but cross-family — commit to a $/hr spend level, "
            "not a specific instance type. Discount applies even after a Graviton migration."
        ),
        savings_mo=round(savings, 2),
        savings_pct=round(discount * 100, 1),
        effort="None",
        risk="Low" if years == 1 else "Medium",
        items=[
            StrategyItem(
                resource_name=r.name,
                from_label=f"on-demand  ${r.monthly_cost:.2f}/mo",
                to_label=f"savings plan {years}yr  ${r.monthly_cost * (1 - discount):.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * (1 - discount),
            )
            for r in eligible
        ],
        caveats=[
            f"Commitment is a $/hr spend level for {years} year(s) — not tied to instance types.",
            "Compute Savings Plans cover EC2, Fargate, and Lambda across all regions.",
            "More flexible than RI: freely switch instance families without losing discount.",
            "Purchase in AWS Cost Explorer → Savings Plans. Stacks with Graviton savings.",
        ],
        migration_cost_est=_MIGRATION_COSTS["aws_savings_plans"],
        break_even_months=_break_even(_MIGRATION_COSTS["aws_savings_plans"], round(savings, 2)),
        priority=2,
    )


def _strategy_aws_spot(resources: list[AnalysisResource]) -> Strategy | None:
    items: list[StrategyItem] = []
    total_savings = 0.0
    total_current = 0.0

    for r in resources:
        if r.service != "compute" or not r.current_provider.startswith("aws_"):
            continue
        family = r.instance_type.split(".")[0] if r.instance_type else ""
        discount = _AWS_SPOT_DISCOUNTS.get(family, 0.0)
        if not discount:
            continue
        saving = r.monthly_cost * discount
        items.append(
            StrategyItem(
                resource_name=r.name,
                from_label=f"{r.instance_type} on-demand  ${r.monthly_cost:.2f}/mo",
                to_label=f"{r.instance_type} spot  ${r.monthly_cost * (1 - discount):.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * (1 - discount),
                note=f"Up to −{int(discount * 100)}% (spot price varies)",
            )
        )
        total_savings += saving
        total_current += r.monthly_cost

    if not items or total_savings < 2.0:
        return None

    return Strategy(
        id="aws_spot",
        name="AWS Spot instances",
        pitch="Interruptible instances for dev, staging, CI runners, or batch workloads.",
        savings_mo=round(total_savings, 2),
        savings_pct=round(total_savings / total_current * 100, 1),
        effort="Low",
        risk="Medium",
        items=items,
        caveats=[
            "Spot instances can be reclaimed with 2 minutes notice.",
            "Suitable for: dev/staging, CI, batch jobs, stateless workers.",
            "Not suitable for: production APIs, databases, or anything requiring uptime.",
            "Spot prices are market-rate — actual savings range from ~40% to ~90%.",
        ],
        migration_cost_est=_MIGRATION_COSTS["aws_spot"],
        break_even_months=_break_even(_MIGRATION_COSTS["aws_spot"], round(total_savings, 2)),
        priority=1,
    )


def _strategy_gcp_preemptible(resources: list[AnalysisResource]) -> Strategy | None:
    items: list[StrategyItem] = []
    total_savings = 0.0
    total_current = 0.0

    for r in resources:
        if r.service != "compute" or r.current_provider != "gcp_gce":
            continue
        series = r.instance_type.split("-")[0] if r.instance_type else ""
        discount = _GCP_PREEMPTIBLE_DISCOUNTS.get(series, 0.0)
        if not discount:
            continue
        saving = r.monthly_cost * discount
        items.append(
            StrategyItem(
                resource_name=r.name,
                from_label=f"{r.instance_type} on-demand  ${r.monthly_cost:.2f}/mo",
                to_label=f"{r.instance_type} preemptible  ${r.monthly_cost * (1 - discount):.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * (1 - discount),
                note=f"Up to −{int(discount * 100)}% preemptible",
            )
        )
        total_savings += saving
        total_current += r.monthly_cost

    if not items or total_savings < 2.0:
        return None

    return Strategy(
        id="gcp_preemptible",
        name="GCP Preemptible / Spot VMs",
        pitch="Preemptible VMs are up to 91% cheaper — ideal for fault-tolerant or batch workloads.",
        savings_mo=round(total_savings, 2),
        savings_pct=round(total_savings / total_current * 100, 1),
        effort="Low",
        risk="Medium",
        items=items,
        caveats=[
            "Preemptible VMs last at most 24 hours and can be stopped at any time.",
            "GCP gives a 30-second shutdown notice — design for graceful termination.",
            "Suitable for: batch jobs, data processing, CI/CD, stateless workers.",
            "Not suitable for: production databases, stateful services, user-facing APIs.",
        ],
        migration_cost_est=_MIGRATION_COSTS["gcp_preemptible"],
        break_even_months=_break_even(_MIGRATION_COSTS["gcp_preemptible"], round(total_savings, 2)),
        priority=1,
    )


def _strategy_rightsize(resources: list[AnalysisResource]) -> Strategy | None:
    """Suggest one size down for instances that are likely over-provisioned."""
    items: list[StrategyItem] = []
    total_savings = 0.0
    total_current = 0.0

    for r in resources:
        if r.service != "compute" or not r.instance_type:
            continue
        smaller = None
        if r.current_provider.startswith("aws_"):
            smaller = _downsize_aws(r.instance_type)
        elif r.current_provider == "gcp_gce":
            smaller = _downsize_gcp(r.instance_type)
        elif r.current_provider.startswith("azure_"):
            smaller = _downsize_azure(r.instance_type)
        if not smaller:
            continue
        # Downsizing halves vCPU and RAM → ~50% cost reduction
        to_cost = r.monthly_cost * 0.50
        saving = r.monthly_cost - to_cost
        items.append(
            StrategyItem(
                resource_name=r.name,
                from_label=f"{r.instance_type}  ${r.monthly_cost:.2f}/mo",
                to_label=f"{smaller}  ${to_cost:.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=to_cost,
                note="if avg CPU < 40%",
            )
        )
        total_savings += saving
        total_current += r.monthly_cost

    if not items or total_savings < 5.0:
        return None

    return Strategy(
        id="rightsize",
        name="Right-size over-provisioned instances",
        pitch="New deployments routinely over-provision by 2×. If average CPU is below 40%, cut the instance in half.",
        savings_mo=round(total_savings, 2),
        savings_pct=round(total_savings / total_current * 100, 1),
        effort="Low",
        risk="Low",
        items=items,
        caveats=[
            "Only applicable if average CPU utilization is consistently below 40%.",
            "Check CloudWatch / Cloud Monitoring metrics before downsizing.",
            "Test at off-peak hours first — roll back is one Terraform apply.",
            "Memory-bound workloads (databases, JVM) may need the full RAM even at low CPU.",
        ],
        migration_cost_est=_MIGRATION_COSTS["rightsize"],
        break_even_months=_break_even(_MIGRATION_COSTS["rightsize"], round(total_savings, 2)),
        priority=1,
    )


def _strategy_instance_scheduler(resources: list[AnalysisResource]) -> Strategy | None:
    """Stop dev/staging compute outside business hours — ~70% cost reduction for non-prod."""
    compute = [r for r in resources if r.service == "compute"]
    if not compute:
        return None
    current_total = sum(r.monthly_cost for r in compute)
    savings = current_total * (1 - _SCHEDULER_UPTIME_FACTOR)
    if savings < 5.0:
        return None

    return Strategy(
        id="instance_scheduler",
        name="Instance Scheduler (non-prod off-hours)",
        pitch=(
            "Stop dev and staging instances 7PM–8AM on weekdays and all weekend. "
            "Business-hours-only: ~30% of the month → ~70% cost reduction for non-prod."
        ),
        savings_mo=round(savings, 2),
        savings_pct=round((1 - _SCHEDULER_UPTIME_FACTOR) * 100, 1),
        effort="Low",
        risk="Low",
        items=[
            StrategyItem(
                resource_name=r.name,
                from_label=f"24/7  ${r.monthly_cost:.2f}/mo",
                to_label=f"business hours  ${r.monthly_cost * _SCHEDULER_UPTIME_FACTOR:.2f}/mo",
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * _SCHEDULER_UPTIME_FACTOR,
                note="if non-production",
            )
            for r in compute
        ],
        caveats=[
            "Only applicable to non-production instances (dev, staging, CI, preview envs).",
            "AWS: use AWS Instance Scheduler or EventBridge Scheduler cron rules.",
            "GCP: Cloud Scheduler + Managed Instance Group min=0 scaling.",
            "Add a manual override to wake instances on demand for late-night work.",
            "Verify stateful services (DBs, queues) survive overnight restarts safely.",
        ],
        migration_cost_est=_MIGRATION_COSTS["instance_scheduler"],
        break_even_months=_break_even(_MIGRATION_COSTS["instance_scheduler"], round(savings, 2)),
        priority=1,
    )


def _strategy_storage_lifecycle(resources: list[AnalysisResource]) -> Strategy | None:
    """S3/GCS lifecycle rules to automatically tier cold data to cheaper storage classes."""
    storage = [
        r
        for r in resources
        if r.service == "storage" and r.current_provider in ("aws_s3", "gcp_storage", "azure_blob")
    ]
    if not storage:
        return None

    current_total = sum(r.monthly_cost for r in storage)
    savings = current_total * _LIFECYCLE_SAVINGS_FACTOR
    if savings < 2.0:
        return None

    return Strategy(
        id="storage_lifecycle",
        name="Storage lifecycle rules (cold-data tiering)",
        pitch=(
            "Add lifecycle rules to automatically move objects to cheaper tiers. "
            "Data untouched 30+ days is archived at 40–96% lower cost with zero code changes."
        ),
        savings_mo=round(savings, 2),
        savings_pct=round(_LIFECYCLE_SAVINGS_FACTOR * 100, 1),
        effort="Low",
        risk="Low",
        items=[
            StrategyItem(
                resource_name=r.name,
                from_label=f"hot tier  ${r.monthly_cost:.2f}/mo",
                to_label=(
                    f"{_COLD_TIER_LABEL.get(r.current_provider, 'cold tier')}"
                    f"  ${r.monthly_cost * (1 - _LIFECYCLE_SAVINGS_FACTOR):.2f}/mo"
                ),
                from_cost=r.monthly_cost,
                to_cost=r.monthly_cost * (1 - _LIFECYCLE_SAVINGS_FACTOR),
                note="40% cold data assumed",
            )
            for r in storage
        ],
        caveats=[
            "S3 Intelligent-Tiering: objects idle 30 days auto-move to IA ($0.00025/1000 objects monitoring fee).",
            "For backups/archives: Glacier Deep Archive at $0.00099/GB vs Standard $0.023/GB (96% cheaper).",
            "GCS: Object Lifecycle Management → Nearline (30 days) → Coldline (90 days).",
            "Retrieval fees apply when accessing archived objects — test your access patterns first.",
            "Add in Terraform: aws_s3_bucket_lifecycle_configuration or google_storage_bucket.lifecycle_rule.",
        ],
        migration_cost_est=_MIGRATION_COSTS["storage_lifecycle"],
        break_even_months=_break_even(_MIGRATION_COSTS["storage_lifecycle"], round(savings, 2)),
        priority=1,
    )


def _strategy_full_migration(resources: list[AnalysisResource]) -> Strategy | None:
    """Move everything to cheapest alternative providers."""
    from .engine import plan_compute, plan_database, plan_object_storage

    items: list[StrategyItem] = []
    current_total = sum(r.monthly_cost for r in resources)
    new_total = 0.0

    for r in resources:
        if r.service == "compute" and r.compute_spec:
            results = plan_compute(r.compute_spec)
            alts = [
                x
                for x in sorted(results, key=lambda x: x.total)
                if x.provider != r.current_provider and x.total < r.monthly_cost
            ]
            if alts:
                best = alts[0]
                items.append(
                    StrategyItem(
                        resource_name=r.name,
                        from_label=f"{r.current_provider}  ${r.monthly_cost:.2f}/mo",
                        to_label=f"{best.display_name} {best.instance_name}  ${best.total:.2f}/mo",
                        from_cost=r.monthly_cost,
                        to_cost=best.total,
                    )
                )
                new_total += best.total
            else:
                new_total += r.monthly_cost
        elif r.service == "storage" and r.storage_spec:
            results = plan_object_storage(r.storage_spec)
            alts = [
                x
                for x in sorted(results, key=lambda x: x.total)
                if x.provider != r.current_provider and x.total < r.monthly_cost
            ]
            if alts:
                best = alts[0]
                items.append(
                    StrategyItem(
                        resource_name=r.name,
                        from_label=f"{r.current_provider}  ${r.monthly_cost:.2f}/mo",
                        to_label=f"{best.display_name}  ${best.total:.2f}/mo",
                        from_cost=r.monthly_cost,
                        to_cost=best.total,
                    )
                )
                new_total += best.total
            else:
                new_total += r.monthly_cost
        elif r.service == "database" and r.database_spec:
            results = plan_database(r.database_spec)
            alts = [
                x
                for x in sorted(results, key=lambda x: x.total)
                if x.provider != r.current_provider and x.total < r.monthly_cost
            ]
            if alts:
                best = alts[0]
                items.append(
                    StrategyItem(
                        resource_name=r.name,
                        from_label=f"{r.current_provider}  ${r.monthly_cost:.2f}/mo",
                        to_label=f"{best.display_name} {best.plan_name}  ${best.total:.2f}/mo",
                        from_cost=r.monthly_cost,
                        to_cost=best.total,
                    )
                )
                new_total += best.total
        else:
            new_total += r.monthly_cost

    if not items:
        return None

    savings = current_total - new_total
    if savings < 5.0:
        return None

    return Strategy(
        id="full_migration",
        name="Full provider migration",
        pitch="Move everything to cheapest alternatives. Biggest savings, highest effort.",
        savings_mo=round(savings, 2),
        savings_pct=round(savings / current_total * 100, 1),
        effort="High",
        risk="High",
        items=items,
        caveats=[
            "Update all service endpoints, environment variables, and DNS records.",
            "Plan 2–4 weeks migration effort depending on stack complexity.",
            "Some managed services (RDS, Cloud SQL) may require self-hosted replacements.",
            "Test thoroughly in a staging environment before cutting over production.",
        ],
        migration_cost_est=_MIGRATION_COSTS["full_migration"],
        break_even_months=_break_even(_MIGRATION_COSTS["full_migration"], round(savings, 2)),
        priority=3,
    )


# ── Portfolio strategy ────────────────────────────────────────────────────────


def _strategy_aws_portfolio(resources: list[AnalysisResource]) -> Strategy | None:
    """Optimal blend of on-demand + 1yr RI + spot (Markowitz-inspired)."""
    aws_compute = [
        r for r in resources if r.service == "compute" and r.current_provider.startswith("aws_")
    ]
    if not aws_compute:
        return None
    current_total = sum(r.monthly_cost for r in aws_compute)

    # Balanced portfolio: 30% on-demand + 40% 1yr RI + 30% spot
    # Each "asset" has a return (discount) and a risk profile.
    # Spot is the highest-return / highest-variance asset; on-demand is the risk-free rate.
    od_frac, ri_frac, spot_frac = 0.30, 0.40, 0.30
    ri_disc = _AWS_RI_DISCOUNTS[1]  # 0.30
    avg_spot = 0.65  # weighted average across common families
    blended = od_frac * 1.0 + ri_frac * (1 - ri_disc) + spot_frac * (1 - avg_spot)
    # = 0.30 + 0.28 + 0.105 = 0.685  →  ~31.5% weighted savings

    new_total = current_total * blended
    savings = current_total - new_total
    if savings < 5.0:
        return None

    items = [
        StrategyItem(
            resource_name=r.name,
            from_label=f"100% on-demand  ${r.monthly_cost:.2f}/mo",
            to_label=(
                f"portfolio blend  ${r.monthly_cost * blended:.2f}/mo  "
                f"[30% OD · 40% 1yr RI · 30% Spot]"
            ),
            from_cost=r.monthly_cost,
            to_cost=r.monthly_cost * blended,
            note=f"~{int((1 - blended) * 100)}% blended discount",
        )
        for r in aws_compute
    ]

    return Strategy(
        id="aws_portfolio",
        name="Balanced commitment portfolio",
        pitch=(
            "Markowitz-optimal mix: 30% on-demand (liquidity) + 40% 1yr RI (core) + 30% Spot (alpha). "
            "Higher expected savings than pure RI with lower commitment risk than all-spot."
        ),
        savings_mo=round(savings, 2),
        savings_pct=round(savings / current_total * 100, 1),
        effort="Low",
        risk="Low",
        items=items,
        caveats=[
            "Spot fraction (~30%) assumes fault-tolerant, stateless workloads.",
            "Adjust split based on your uptime requirements: more RI = more stability.",
            "Re-balance quarterly as usage patterns change.",
            "RI portion requires 1yr commitment — verify instance family stability first.",
        ],
        migration_cost_est=_MIGRATION_COSTS["portfolio"],
        break_even_months=_break_even(_MIGRATION_COSTS["portfolio"], round(savings, 2)),
        priority=2,
    )


# ── Orchestrator ──────────────────────────────────────────────────────────────

_EFFORT_RANK = {"None": 0, "Low": 1, "Medium": 2, "High": 3}
_RISK_RANK = {"Low": 0, "Medium": 1, "High": 2}


def _mark_dominant(strategies: list[Strategy]) -> None:
    """Mark strategies that are not Pareto-dominated on savings / effort / risk.

    A strategy is dominant if no other strategy beats it on ALL three dimensions
    simultaneously. These are the "no trade-off" moves worth highlighting first.
    """
    for s in strategies:
        s.is_dominant = not any(
            other is not s
            and other.savings_mo >= s.savings_mo
            and _EFFORT_RANK[other.effort] <= _EFFORT_RANK[s.effort]
            and _RISK_RANK[other.risk] <= _RISK_RANK[s.risk]
            and (
                other.savings_mo > s.savings_mo
                or _EFFORT_RANK[other.effort] < _EFFORT_RANK[s.effort]
                or _RISK_RANK[other.risk] < _RISK_RANK[s.risk]
            )
            for other in strategies
        )


def run_all_strategies(resources: list[AnalysisResource]) -> list[Strategy]:
    """Run all applicable strategy generators. Result set varies by provider and instance types."""
    generators = [
        # AWS-specific
        lambda: _strategy_region_shift(resources),
        lambda: _strategy_graviton(resources),
        lambda: _strategy_aws_spot(resources),
        lambda: _strategy_aws_reserved(resources, 1),
        lambda: _strategy_aws_reserved(resources, 3),
        lambda: _strategy_aws_savings_plans(resources, 1),
        lambda: _strategy_aws_savings_plans(resources, 3),
        lambda: _strategy_aws_portfolio(resources),
        # GCP-specific
        lambda: _strategy_gcp_cud(resources, 1),
        lambda: _strategy_gcp_cud(resources, 3),
        lambda: _strategy_gcp_preemptible(resources),
        # Azure-specific
        lambda: _strategy_azure_ri(resources, 1),
        lambda: _strategy_azure_ri(resources, 3),
        # Provider-agnostic
        lambda: _strategy_rightsize(resources),
        lambda: _strategy_instance_scheduler(resources),
        lambda: _strategy_storage_lifecycle(resources),
        lambda: _strategy_full_migration(resources),
    ]

    strategies: list[Strategy] = []
    for gen in generators:
        try:
            s = gen()
            if s:
                strategies.append(s)
        except Exception:
            pass

    strategies.sort(key=lambda s: (s.priority, -s.savings_mo))
    _mark_dominant(strategies)
    return strategies
