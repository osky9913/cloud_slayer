from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...models import ComputeResult, ComputeSpec
from ...pricing import PricingUnavailableError


@dataclass
class InstanceType:
    name: str
    vcpu: int
    memory_gb: float
    price_per_month: float
    notes: str = ""
    price_source: str = ""
    source_url: str = ""


class ComputeProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    def catalog(self) -> list[InstanceType]: ...

    def find_match(self, spec: ComputeSpec) -> InstanceType | None:
        candidates = [
            i for i in self.catalog() if i.vcpu >= spec.vcpu and i.memory_gb >= spec.memory_gb
        ]
        return min(candidates, key=lambda i: i.price_per_month) if candidates else None

    def calculate_cost(self, spec: ComputeSpec, instance_name: str = "") -> ComputeResult | None:
        match = (
            next((item for item in self.catalog() if item.name == instance_name), None)
            if instance_name
            else self.find_match(spec)
        )
        if instance_name and match is None:
            raise PricingUnavailableError(
                self.display_name,
                f"no price was returned for exact instance {instance_name!r}",
            )
        if not match:
            return None
        return ComputeResult(
            provider=self.name,
            display_name=self.display_name,
            instance_name=match.name,
            instance_vcpu=match.vcpu,
            instance_memory_gb=match.memory_gb,
            price_per_month=match.price_per_month,
            notes=match.notes,
            price_source=match.price_source,
            source_url=match.source_url,
        )
