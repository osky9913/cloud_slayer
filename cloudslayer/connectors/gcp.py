"""GCP connector — reads running resources via Cloud Asset Inventory / SDK.

Zero setup beyond:  gcloud auth application-default login
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from ..models import ComputeSpec, DatabaseSpec, ObjectStorageSpec

_HOURS_PER_MONTH = 730.0
_STORAGE_PER_GB_MONTH = 0.17  # GCP SSD persistent disk, us-east1


@dataclass
class GCPActualResource:
    service: str  # "compute" | "database"
    display_name: str
    actual_monthly_cost: float
    current_provider: str  # "gcp_gce" | "gcp_cloudsql"
    instance_type: str
    count: int = 1
    compute_spec: ComputeSpec | None = None
    storage_spec: ObjectStorageSpec | None = None
    database_spec: DatabaseSpec | None = None


class GCPConnector:
    """Reads running GCP resources and estimates monthly cost from catalog prices."""

    def __init__(self, project: str = ""):
        try:
            from google.cloud import compute_v1

            self._compute_v1 = compute_v1
        except ImportError:
            raise RuntimeError(
                "google-cloud-compute is required for GCP integration.\n"
                "Install with:  pip install 'cloudslayer[gcp]'\n"
                "           or: uv add 'cloudslayer[gcp]'"
            )
        self._project = project or self._get_project()

    # ── Project detection ─────────────────────────────────────────────────────

    def _get_project(self) -> str:
        for env_var in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
            val = os.environ.get(env_var, "").strip()
            if val:
                return val
        try:
            result = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            project = result.stdout.strip()
            if project and project != "(unset)":
                return project
        except Exception:
            pass
        raise RuntimeError(
            "Could not determine GCP project ID.\n\n"
            "Set one of:\n"
            "  • GOOGLE_CLOUD_PROJECT environment variable\n"
            "  • GCLOUD_PROJECT environment variable\n"
            "  • gcloud config set project <project-id>\n"
            "  • cloudslayer actual --project <project-id>"
        )

    # ── Credential validation ─────────────────────────────────────────────────

    def validate_credentials(self) -> str:
        """Returns project ID if ADC credentials work, raises RuntimeError otherwise."""
        try:
            import google.auth

            _credentials, project = google.auth.default()
            return project or self._project
        except Exception as e:
            raise RuntimeError(
                f"GCP credentials not found or invalid: {e}\n\n"
                "Run:  gcloud auth application-default login\n\n"
                "Required APIs: compute.googleapis.com, sqladmin.googleapis.com"
            )

    # ── Public interface ──────────────────────────────────────────────────────

    def get_spend(self, days: int = 30) -> list[GCPActualResource]:
        """Return estimated monthly costs for all running GCP resources.

        The ``days`` parameter is accepted for API compatibility with other
        connectors but is not used — this connector reads *current* running
        resources rather than historical billing data.
        """
        resources: list[GCPActualResource] = []
        resources.extend(self._gce())
        resources.extend(self._cloudsql())
        return resources

    # ── GCE ───────────────────────────────────────────────────────────────────

    def _gce(self) -> list[GCPActualResource]:
        try:
            from ..providers.compute.gcp_gce import _CATALOG
            from ..scanner import GCP_INSTANCE_SPECS

            client = self._compute_v1.InstancesClient()
            counts: dict[str, int] = {}

            for _zone, instances_scoped in client.aggregated_list(project=self._project):
                for instance in instances_scoped.instances or []:
                    if instance.status != "RUNNING":
                        continue
                    # machine_type is a full URL; extract the type name from the suffix
                    # e.g. "zones/us-east1-b/machineTypes/e2-medium" → "e2-medium"
                    machine_type = instance.machine_type.rsplit("/", 1)[-1]
                    counts[machine_type] = counts.get(machine_type, 0) + 1

            # Build a fast lookup: name → monthly price
            price_map: dict[str, float] = {it.name: it.price_per_month for it in _CATALOG}

            results: list[GCPActualResource] = []
            for machine_type, count in counts.items():
                hourly = price_map.get(machine_type, 0.0) / _HOURS_PER_MONTH
                monthly = hourly * _HOURS_PER_MONTH * count

                vcpu, mem = GCP_INSTANCE_SPECS.get(machine_type, (2, 4.0))
                slug = machine_type.replace(".", "-")

                results.append(
                    GCPActualResource(
                        service="compute",
                        display_name=f"{machine_type} × {count}" if count > 1 else machine_type,
                        actual_monthly_cost=monthly,
                        current_provider="gcp_gce",
                        instance_type=machine_type,
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

    # ── Cloud SQL ─────────────────────────────────────────────────────────────

    def _cloudsql(self) -> list[GCPActualResource]:
        try:
            import google.auth
            import google.auth.transport.requests

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            session = google.auth.transport.requests.AuthorizedSession(credentials)

            url = f"https://sqladmin.googleapis.com/sql/v1beta4/projects/{self._project}/instances"
            response = session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            from ..providers.database.gcp_cloudsql import _PLANS

            # Build lookup: tier name → plan
            plan_map: dict[str, object] = {p.name: p for p in _PLANS}

            results: list[GCPActualResource] = []
            for item in data.get("items", []):
                if item.get("state") != "RUNNABLE":
                    continue

                tier = item.get("settings", {}).get("tier", "")
                instance_name = item.get("name", tier)

                # Resolve cost from catalog; fall back to 0 for unknown tiers
                plan = plan_map.get(tier)
                if plan is not None:
                    storage_gb = float(
                        item.get("settings", {}).get("dataDiskSizeGb", plan.included_storage_gb)
                    )
                    extra_storage = max(0.0, storage_gb - plan.included_storage_gb)
                    monthly = plan.base_price + extra_storage * plan.storage_per_gb
                    vcpu = plan.vcpu
                    mem = plan.memory_gb
                else:
                    storage_gb = float(item.get("settings", {}).get("dataDiskSizeGb", 10))
                    monthly = storage_gb * _STORAGE_PER_GB_MONTH
                    vcpu, mem = 1, 1.75

                slug = instance_name.replace("_", "-").replace(":", "-")

                results.append(
                    GCPActualResource(
                        service="database",
                        display_name=instance_name,
                        actual_monthly_cost=monthly,
                        current_provider="gcp_cloudsql",
                        instance_type=tier,
                        count=1,
                        database_spec=DatabaseSpec(
                            name=slug,
                            vcpu=vcpu,
                            memory_gb=mem,
                            storage_gb=storage_gb,
                            engine="postgres",
                        ),
                    )
                )

            return sorted(results, key=lambda r: r.actual_monthly_cost, reverse=True)

        except Exception:
            return []
