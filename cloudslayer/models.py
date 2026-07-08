from __future__ import annotations

from dataclasses import dataclass

# ── Object Storage ────────────────────────────────────────────────────────────


@dataclass
class ObjectStorageSpec:
    name: str
    storage_gb: float
    get_requests: int = 0
    put_requests: int = 0
    egress_gb: float = 0.0


@dataclass
class StoragePricing:
    provider: str
    display_name: str
    storage_per_gb_mo: float
    get_per_million: float
    put_per_million: float
    egress_per_gb: float
    free_storage_gb: float = 0.0
    free_egress_gb: float = 0.0
    min_storage_gb: float = 0.0
    notes: str = ""
    source_url: str = ""
    last_verified: str = ""


@dataclass
class CostResult:
    provider: str
    display_name: str
    storage_cost: float
    request_cost: float
    egress_cost: float
    notes: str = ""

    @property
    def total(self) -> float:
        return self.storage_cost + self.request_cost + self.egress_cost


# ── Compute ───────────────────────────────────────────────────────────────────


@dataclass
class ComputeSpec:
    name: str
    vcpu: int
    memory_gb: float


@dataclass
class ComputeResult:
    provider: str
    display_name: str
    instance_name: str
    instance_vcpu: int
    instance_memory_gb: float
    price_per_month: float
    notes: str = ""

    @property
    def total(self) -> float:
        return self.price_per_month


# ── Database ──────────────────────────────────────────────────────────────────


@dataclass
class DatabaseSpec:
    name: str
    vcpu: int = 1
    memory_gb: float = 1.0
    storage_gb: float = 20.0
    engine: str = "postgres"


@dataclass
class DatabaseResult:
    provider: str
    display_name: str
    plan_name: str
    plan_vcpu: int
    plan_memory_gb: float
    instance_cost: float
    storage_cost: float
    notes: str = ""

    @property
    def total(self) -> float:
        return self.instance_cost + self.storage_cost


# ── Serverless ────────────────────────────────────────────────────────────────


@dataclass
class ServerlessSpec:
    name: str
    invocations_per_month: int = 1_000_000
    avg_duration_ms: float = 100.0
    memory_mb: int = 256


@dataclass
class ServerlessResult:
    provider: str
    display_name: str
    monthly_cost: float
    per_million_requests: float
    notes: str = ""
