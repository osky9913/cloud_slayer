# Contributing to cloudslayer

Thanks for wanting to make cloudslayer better. This guide covers the two most common contributions: adding a provider and reporting a bug.

## Quick start

```bash
git clone https://github.com/cloudslayer-dev/cloudslayer
cd cloudslayer
uv sync --extra dev
uv run cloudslayer plan examples/basic.hcl   # verify it works
uv run pytest                            # run the test suite
```

## Scope

cloudslayer focuses on **AWS, GCP, and Azure**. The most valuable contributions right now are:

1. **New resource types** within the big-3 (NAT gateways, load balancers, EBS/managed disks, node groups, caches) — see `UNCOSTED_RESOURCES` in `cloudslayer/scanner.py` for the wishlist
2. **More instance/plan coverage** in the existing providers
3. **New strategies** for the analyze engine

PRs adding other cloud providers will generally not be merged — keeping prices verifiable from official APIs is a core promise of the project.

## Adding a provider

Providers live under `cloudslayer/providers/<resource-type>/<cloud>/` (e.g. `cloudslayer/providers/database/azure/postgres.py`).

### 1. Compute provider

Create `cloudslayer/providers/compute/<cloud>/<name>.py`:

```python
from .base import ComputeProvider, InstanceType

# https://provider.com/pricing — verified YYYY-MM-DD
_CATALOG = [
    InstanceType("instance-type", vcpu=2, memory_gb=4, price_per_month=20.00, notes="..."),
    # ...
]

class MyProvider(ComputeProvider):
    @property
    def name(self) -> str:
        return "my_provider"   # used in JSON output and --provider filter

    @property
    def display_name(self) -> str:
        return "My Provider"   # shown in terminal output

    def catalog(self) -> list[InstanceType]:
        return _CATALOG
```

Then add it to `cloudslayer/providers/compute/__init__.py`.

### 2. Object storage provider

Create `cloudslayer/providers/storage/<cloud>/<name>.py`:

```python
from ..base import ObjectStorageProvider
from ....models import StoragePricing

_PRICING = StoragePricing(
    provider="my_provider",
    display_name="My Provider",
    storage_per_gb_mo=0.020,
    get_per_million=0.40,
    put_per_million=5.00,
    egress_per_gb=0.09,
    free_storage_gb=0.0,
    notes="...",
    source_url="https://provider.com/pricing",
    last_verified="YYYY-MM-DD",
)

class MyProvider(ObjectStorageProvider):
    ...
```

Then add it to `cloudslayer/providers/storage/__init__.py`.

### 3. Database provider

Create `cloudslayer/providers/database/<cloud>/<name>.py`:

```python
from .base import DatabaseProvider, DatabasePlan

_PLANS = [
    DatabasePlan("Starter", vcpu=2, memory_gb=4, base_price=29.00,
                 storage_per_gb=0.10, included_storage_gb=10.0, notes="..."),
    # ...
]

class MyDBProvider(DatabaseProvider):
    ...
```

### Pricing rules

- **Always include a source URL** and `last_verified` date in the file header comment
- **Prefer live pricing** — AWS (Bulk Pricing API) and Azure (Retail Prices API) have free, unauthenticated pricing endpoints; follow the pattern in `cloudslayer/providers/database/aws/rds.py`
- **Live pricing must fall back gracefully** to verified hardcoded prices if the API is unreachable
- Prices should be in **USD**, East US / us-east-1 region (or documented otherwise)
- Use **on-demand / pay-as-you-go** pricing (no reserved instances)

## Adding tests

Add tests for your provider in `tests/test_providers.py`:

```python
class TestMyProvider:
    def setup_method(self):
        self.provider = MyProvider()

    def test_basic_match(self):
        spec = ComputeSpec(name="x", vcpu=2, memory_gb=4)
        result = self.provider.calculate_cost(spec)
        assert result is not None
        assert result.price_per_month == pytest.approx(20.00, abs=0.01)

    def test_no_match_returns_none(self):
        spec = ComputeSpec(name="x", vcpu=9999, memory_gb=9999)
        assert self.provider.calculate_cost(spec) is None
```

## Running tests

```bash
uv run pytest                          # all tests
uv run pytest tests/test_providers.py  # just provider tests
uv run pytest -v --tb=short            # verbose with short tracebacks
uv run pytest --cov=cloudslayer            # with coverage
```

## Code style

We use `ruff` for linting and formatting:

```bash
uv run ruff check cloudslayer/
uv run ruff format cloudslayer/
```

## PR checklist

Before opening a PR, confirm:

- [ ] Tests pass (`uv run pytest`)
- [ ] Pricing source URL and verification date in code comments
- [ ] `cloudslayer providers` shows your new provider
- [ ] `cloudslayer plan examples/basic.hcl` runs without errors

## Questions?

Open a [GitHub Discussion](https://github.com/cloudslayer-dev/cloudslayer/discussions) — we're happy to help.
