from ....config import fallback_prices_enabled
from ....models import ServerlessSpec
from ....pricing import PricingUnavailableError
from ..base import ServerlessProvider

_REQUEST_PRICE = 0.0000002  # per execution ($0.20/million)
_COMPUTE_PRICE = 0.000016  # per GB-second
_FREE_REQUESTS = 1_000_000
_FREE_GB_SECONDS = 400_000


class AzureFunctionsProvider(ServerlessProvider):
    @property
    def name(self) -> str:
        return "azure_functions"

    @property
    def display_name(self) -> str:
        return "Azure Functions"

    def calculate_cost(self, spec: ServerlessSpec):
        if not fallback_prices_enabled():
            raise PricingUnavailableError(
                self.display_name,
                "live serverless pricing is not implemented; rerun with --fallback to use the published static tariff",
            )
        return super().calculate_cost(spec)

    def _monthly_cost(self, spec: ServerlessSpec) -> float:
        billable_requests = max(0, spec.invocations_per_month - _FREE_REQUESTS)
        gb_seconds = (
            (spec.memory_mb / 1024) * (spec.avg_duration_ms / 1000) * spec.invocations_per_month
        )
        billable_gb_seconds = max(0.0, gb_seconds - _FREE_GB_SECONDS)
        return billable_requests * _REQUEST_PRICE + billable_gb_seconds * _COMPUTE_PRICE

    def _notes(self) -> str:
        return "1M req/mo + 400K GB-s free (Consumption)"

    def _source_url(self) -> str:
        return "https://azure.microsoft.com/pricing/details/functions/"
