"""Pricing availability and provenance helpers."""

from __future__ import annotations


class PricingUnavailableError(RuntimeError):
    """Raised when a provider has neither live nor usable cached pricing."""

    def __init__(self, provider: str, detail: str) -> None:
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider}: {detail}")


_WARNINGS: dict[str, str] = {}


def clear_pricing_warnings() -> None:
    _WARNINGS.clear()


def record_pricing_warning(error: PricingUnavailableError) -> None:
    _WARNINGS[error.provider] = error.detail


def pricing_warnings() -> list[tuple[str, str]]:
    return sorted(_WARNINGS.items())
