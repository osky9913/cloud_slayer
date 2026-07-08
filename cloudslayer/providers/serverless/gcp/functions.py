from ....models import ServerlessSpec
from ..base import ServerlessProvider

_REQUEST_PRICE = 0.0000004  # per invocation ($0.40/million) — Gen 1
_CPU_PRICE = 0.0000100  # per GHz-second
_RAM_PRICE = 0.0000025  # per GB-second
_FREE_REQUESTS = 2_000_000
_FREE_GB_SECONDS = 400_000
_FREE_GHZ_SECONDS = 200_000
_DEFAULT_GHZ = 0.2  # 200MHz allocated per 256MB function


class GCPFunctionsProvider(ServerlessProvider):
    @property
    def name(self) -> str:
        return "gcp_functions"

    @property
    def display_name(self) -> str:
        return "GCP Cloud Functions"

    def _monthly_cost(self, spec: ServerlessSpec) -> float:
        billable_requests = max(0, spec.invocations_per_month - _FREE_REQUESTS)
        duration_s = spec.avg_duration_ms / 1000
        gb_seconds = (spec.memory_mb / 1024) * duration_s * spec.invocations_per_month
        ghz_seconds = _DEFAULT_GHZ * duration_s * spec.invocations_per_month
        billable_gb_s = max(0.0, gb_seconds - _FREE_GB_SECONDS)
        billable_ghz_s = max(0.0, ghz_seconds - _FREE_GHZ_SECONDS)
        return (
            billable_requests * _REQUEST_PRICE
            + billable_gb_s * _RAM_PRICE
            + billable_ghz_s * _CPU_PRICE
        )

    def _notes(self) -> str:
        return "2M req/mo + 400K GB-s free (Gen 1)"
