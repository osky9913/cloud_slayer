from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...models import DatabaseResult, DatabaseSpec
from ...pricing import PricingUnavailableError


@dataclass
class DatabasePlan:
    name: str
    vcpu: int
    memory_gb: float
    base_price: float
    storage_per_gb: float
    included_storage_gb: float = 0.0
    notes: str = ""
    price_source: str = ""
    source_url: str = ""


class DatabaseProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    def plans(self) -> list[DatabasePlan]: ...

    def find_match(self, spec: DatabaseSpec) -> DatabasePlan | None:
        candidates = [
            p for p in self.plans() if p.vcpu >= spec.vcpu and p.memory_gb >= spec.memory_gb
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda p: (
                p.base_price + max(0, spec.storage_gb - p.included_storage_gb) * p.storage_per_gb
            ),
        )

    def calculate_cost(self, spec: DatabaseSpec, plan_name: str = "") -> DatabaseResult | None:
        match = (
            next((plan for plan in self.plans() if plan.name == plan_name), None)
            if plan_name
            else self.find_match(spec)
        )
        if plan_name and match is None:
            raise PricingUnavailableError(
                self.display_name,
                f"no price was returned for exact plan {plan_name!r}",
            )
        if not match:
            return None
        extra_storage = max(0.0, spec.storage_gb - match.included_storage_gb)
        storage_cost = extra_storage * match.storage_per_gb
        return DatabaseResult(
            provider=self.name,
            display_name=self.display_name,
            plan_name=match.name,
            plan_vcpu=match.vcpu,
            plan_memory_gb=match.memory_gb,
            instance_cost=match.base_price,
            storage_cost=storage_cost,
            notes=match.notes,
            price_source=match.price_source,
            source_url=match.source_url,
        )
