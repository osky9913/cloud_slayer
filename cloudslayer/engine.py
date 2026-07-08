from .models import (
    ComputeResult,
    ComputeSpec,
    CostResult,
    DatabaseResult,
    DatabaseSpec,
    ObjectStorageSpec,
    ServerlessResult,
    ServerlessSpec,
)
from .providers import ALL_OBJECT_STORAGE_PROVIDERS
from .providers.compute import ALL_COMPUTE_PROVIDERS
from .providers.database import ALL_DATABASE_PROVIDERS


def plan_object_storage(spec: ObjectStorageSpec) -> list[CostResult]:
    results = [p.calculate_cost(spec) for p in ALL_OBJECT_STORAGE_PROVIDERS]
    return sorted(results, key=lambda r: r.total)


def plan_compute(spec: ComputeSpec) -> list[ComputeResult]:
    results = [p.calculate_cost(spec) for p in ALL_COMPUTE_PROVIDERS]
    return sorted([r for r in results if r is not None], key=lambda r: r.total)


def plan_database(spec: DatabaseSpec) -> list[DatabaseResult]:
    results = [p.calculate_cost(spec) for p in ALL_DATABASE_PROVIDERS]
    return sorted([r for r in results if r is not None], key=lambda r: r.total)


def plan_serverless(spec: ServerlessSpec) -> list[ServerlessResult]:
    from .providers.serverless import ALL_SERVERLESS_PROVIDERS

    results = [p.calculate_cost(spec) for p in ALL_SERVERLESS_PROVIDERS]
    return sorted(results, key=lambda r: r.monthly_cost)
