from __future__ import annotations

from abc import ABC, abstractmethod

from ...models import ServerlessResult, ServerlessSpec


class ServerlessProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    def calculate_cost(self, spec: ServerlessSpec) -> ServerlessResult:
        monthly = self._monthly_cost(spec)
        per_million = self._monthly_cost(
            ServerlessSpec("_", 1_000_000, spec.avg_duration_ms, spec.memory_mb)
        )
        return ServerlessResult(
            provider=self.name,
            display_name=self.display_name,
            monthly_cost=round(monthly, 4),
            per_million_requests=round(per_million, 4),
            notes=self._notes(),
            price_source=self._price_source(),
            source_url=self._source_url(),
        )

    @abstractmethod
    def _monthly_cost(self, spec: ServerlessSpec) -> float: ...

    def _notes(self) -> str:
        return ""

    def _price_source(self) -> str:
        return "fallback"

    def _source_url(self) -> str:
        return ""
