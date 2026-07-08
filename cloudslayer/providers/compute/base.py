from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...models import ComputeResult, ComputeSpec


@dataclass
class InstanceType:
    name: str
    vcpu: int
    memory_gb: float
    price_per_month: float
    notes: str = ""


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

    def calculate_cost(self, spec: ComputeSpec) -> ComputeResult | None:
        match = self.find_match(spec)
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
        )
