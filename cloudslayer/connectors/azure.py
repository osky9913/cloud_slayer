"""Azure connector — lists running VMs and estimates cost from the Azure pricing catalog.

NOTE: ``virtual_machines.list_all()`` returns all *provisioned* VMs regardless of
power state.  Deallocated VMs do not incur compute charges, so the reported cost may
be slightly higher than the actual Azure invoice.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from ..models import ComputeSpec, DatabaseSpec, ObjectStorageSpec

_HOURS_PER_MONTH = 730.0


@dataclass
class AzureActualResource:
    service: str  # "compute"
    display_name: str
    actual_monthly_cost: float
    current_provider: str  # "azure_vm"
    instance_type: str
    count: int = 1
    compute_spec: ComputeSpec | None = None
    storage_spec: ObjectStorageSpec | None = None
    database_spec: DatabaseSpec | None = None


class AzureConnector:
    """Lists Azure VMs and estimates monthly cost from the cloudslayer Azure pricing catalog."""

    def __init__(self, subscription_id: str = "") -> None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.compute import ComputeManagementClient
        except ImportError:
            raise RuntimeError(
                "azure-mgmt-compute and azure-identity are required for Azure integration.\n"
                "Install with:  pip install 'cloudslayer[azure]'\n"
                "           or: uv add 'cloudslayer[azure]'"
            )
        self._ComputeManagementClient = ComputeManagementClient
        self._DefaultAzureCredential = DefaultAzureCredential
        self._subscription_id = subscription_id or self._detect_subscription()

    # ── Subscription detection ────────────────────────────────────────────────

    def _detect_subscription(self) -> str:
        """Return a subscription ID from env var or ``az account show``."""
        env_sub = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
        if env_sub:
            return env_sub

        try:
            result = subprocess.run(
                ["az", "account", "show", "--query", "id", "-o", "tsv"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            sub = result.stdout.strip()
            if sub:
                return sub
        except Exception:
            pass

        raise RuntimeError(
            "Azure subscription ID not found.\n\n"
            "Set it via one of:\n"
            "  • export AZURE_SUBSCRIPTION_ID=<your-subscription-id>\n"
            "  • Run: az login  (then az account set --subscription <id>)\n"
            "  • Pass it directly: AzureConnector(subscription_id='...')"
        )

    # ── Credential validation ─────────────────────────────────────────────────

    def validate_credentials(self) -> str:
        """Return the subscription ID if credentials are valid, raise RuntimeError otherwise."""
        try:
            credential = self._DefaultAzureCredential()
            compute_client = self._ComputeManagementClient(credential, self._subscription_id)
            try:
                next(iter(compute_client.virtual_machines.list_all()))
            except StopIteration:
                pass  # No VMs — credentials are still valid
        except Exception as exc:
            raise RuntimeError(
                f"Azure credentials not found or invalid: {exc}\n\n"
                "Make sure you have Azure credentials configured:\n"
                "  • Run: az login\n"
                "  • Or set: AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID\n"
                "  • Or use: cloudslayer actual --subscription <subscription-id>\n\n"
                "Required permission: Reader role on the subscription"
            ) from exc
        return self._subscription_id

    # ── Public API ────────────────────────────────────────────────────────────

    def get_spend(self, days: int = 30) -> list[AzureActualResource]:
        """Return a list of Azure resources with estimated monthly cost.

        ``days`` is accepted for API compatibility but is not used — Azure VM
        costs are derived from catalog prices, not historical billing data.
        """
        return self._compute()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _compute(self) -> list[AzureActualResource]:
        """List all provisioned VMs and estimate monthly cost from the catalog."""
        try:
            from ..providers.compute.azure import AzureComputeProvider

            credential = self._DefaultAzureCredential()
            compute_client = self._ComputeManagementClient(credential, self._subscription_id)

            # Build a price lookup: vm_size -> (price_per_month, vcpu, memory_gb)
            catalog = AzureComputeProvider().catalog()
            price_map: dict[str, tuple[float, int, float]] = {
                inst.name: (inst.price_per_month, inst.vcpu, inst.memory_gb) for inst in catalog
            }

            # Aggregate VMs by size
            # cost_by_size: vm_size -> (total_monthly_cost, count, vcpu, memory_gb)
            cost_by_size: dict[str, list] = {}

            for vm in compute_client.virtual_machines.list_all():
                vm_size: str = (vm.hardware_profile.vm_size or "").strip()
                if not vm_size:
                    continue

                if vm_size in price_map:
                    price, vcpu, mem = price_map[vm_size]
                else:
                    # Unknown size — skip pricing, count only
                    price, vcpu, mem = 0.0, 0, 0.0

                if vm_size not in cost_by_size:
                    cost_by_size[vm_size] = [0.0, 0, vcpu, mem]
                cost_by_size[vm_size][0] += price
                cost_by_size[vm_size][1] += 1

            results: list[AzureActualResource] = []
            for vm_size, (total_cost, count, vcpu, mem) in cost_by_size.items():
                if total_cost <= 0.0:
                    continue

                slug = vm_size.replace("_", "-").lower()
                results.append(
                    AzureActualResource(
                        service="compute",
                        display_name=f"{vm_size} × {count}" if count > 1 else vm_size,
                        actual_monthly_cost=total_cost,
                        current_provider="azure_vm",
                        instance_type=vm_size,
                        count=count,
                        compute_spec=ComputeSpec(
                            name=slug,
                            vcpu=vcpu * count,
                            memory_gb=mem * count,
                        ),
                    )
                )

            return sorted(results, key=lambda r: r.actual_monthly_cost, reverse=True)

        except Exception:
            return []
