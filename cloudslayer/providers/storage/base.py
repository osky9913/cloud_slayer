from abc import ABC, abstractmethod

from ...models import CostResult, ObjectStorageSpec, StoragePricing


class ObjectStorageProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    def get_pricing(self) -> StoragePricing: ...

    def calculate_cost(self, spec: ObjectStorageSpec) -> CostResult:
        pricing = self.get_pricing()

        billable_storage = max(0.0, spec.storage_gb - pricing.free_storage_gb)
        # Some providers bill a minimum storage amount regardless of usage
        if pricing.min_storage_gb > 0:
            billable_storage = max(pricing.min_storage_gb, billable_storage)
        billable_egress = max(0.0, spec.egress_gb - pricing.free_egress_gb)

        storage_cost = billable_storage * pricing.storage_per_gb_mo
        get_cost = (spec.get_requests / 1_000_000) * pricing.get_per_million
        put_cost = (spec.put_requests / 1_000_000) * pricing.put_per_million
        egress_cost = billable_egress * pricing.egress_per_gb

        return CostResult(
            provider=self.name,
            display_name=self.display_name,
            storage_cost=storage_cost,
            request_cost=get_cost + put_cost,
            egress_cost=egress_cost,
            notes=pricing.notes,
        )
