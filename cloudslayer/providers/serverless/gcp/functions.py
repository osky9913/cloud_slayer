"""GCP Cloud Functions provider.

Static rates were intentionally removed. The provider remains registered so
the CLI can report that live pricing is unavailable instead of silently
dropping GCP from a serverless comparison.
"""

from ....models import ServerlessSpec
from ....pricing import PricingUnavailableError
from ..base import ServerlessProvider


class GCPFunctionsProvider(ServerlessProvider):
    @property
    def name(self) -> str:
        return "gcp_functions"

    @property
    def display_name(self) -> str:
        return "GCP Cloud Functions"

    def calculate_cost(self, spec: ServerlessSpec):
        raise PricingUnavailableError(
            self.display_name,
            "hard-coded rates were removed; live Cloud Billing pricing is not implemented for Cloud Functions",
        )

    def _monthly_cost(self, spec: ServerlessSpec) -> float:
        raise AssertionError("unreachable")
