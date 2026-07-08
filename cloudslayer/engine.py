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
from .pricing import PricingUnavailableError, record_pricing_warning
from .providers import ALL_OBJECT_STORAGE_PROVIDERS
from .providers.compute import ALL_COMPUTE_PROVIDERS
from .providers.database import ALL_DATABASE_PROVIDERS


def _available(callables: list) -> list:
    results = []
    for provider, calculate in callables:
        try:
            result = calculate()
        except PricingUnavailableError as error:
            record_pricing_warning(error)
            continue
        if result is not None:
            results.append(result)
    return results


def plan_object_storage(spec: ObjectStorageSpec) -> list[CostResult]:
    results = _available(
        [(p, lambda p=p: p.calculate_cost(spec)) for p in ALL_OBJECT_STORAGE_PROVIDERS]
    )
    return sorted(results, key=lambda r: r.total)


def plan_compute(
    spec: ComputeSpec, current_provider: str = "", instance_name: str = ""
) -> list[ComputeResult]:
    results = _available(
        [
            (
                p,
                lambda p=p: p.calculate_cost(
                    spec,
                    instance_name if p.name == current_provider else "",
                ),
            )
            for p in ALL_COMPUTE_PROVIDERS
        ]
    )
    return sorted(results, key=lambda r: r.total)


def plan_database(
    spec: DatabaseSpec, current_provider: str = "", plan_name: str = ""
) -> list[DatabaseResult]:
    results = _available(
        [
            (
                p,
                lambda p=p: p.calculate_cost(
                    spec,
                    plan_name if p.name == current_provider else "",
                ),
            )
            for p in ALL_DATABASE_PROVIDERS
        ]
    )
    return sorted(results, key=lambda r: r.total)


def plan_serverless(spec: ServerlessSpec) -> list[ServerlessResult]:
    from .providers.serverless import ALL_SERVERLESS_PROVIDERS

    results = _available(
        [(p, lambda p=p: p.calculate_cost(spec)) for p in ALL_SERVERLESS_PROVIDERS]
    )
    return sorted(results, key=lambda r: r.monthly_cost)
