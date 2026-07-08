from ....config import fallback_prices_enabled
from ....models import ServerlessSpec
from ....pricing import PricingUnavailableError
from ..base import ServerlessProvider

_REQUEST_PRICE = 0.0000002  # per invocation ($0.20/million)
_COMPUTE_PRICE = 0.0000166667  # per GB-second
_FREE_REQUESTS = 1_000_000  # per month
_FREE_GB_SECONDS = 400_000  # per month


class AWSLambdaProvider(ServerlessProvider):
    @property
    def name(self) -> str:
        return "aws_lambda"

    @property
    def display_name(self) -> str:
        return "AWS Lambda"

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
        return "1M req/mo + 400K GB-s free"

    def _source_url(self) -> str:
        return "https://aws.amazon.com/lambda/pricing/"
