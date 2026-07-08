"""Terraform scanner — maps .tf files or `terraform show -json` plans to cloudslayer spec blocks."""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from pathlib import Path

import hcl2

from .models import ComputeSpec, DatabaseSpec, ObjectStorageSpec, ServerlessSpec

# Maps Terraform resource types → cloudslayer resource type
RESOURCE_MAP: dict[str, str] = {
    "aws_s3_bucket": "object_storage",
    "aws_s3_bucket_v2": "object_storage",
    "google_storage_bucket": "object_storage",
    "azurerm_storage_account": "object_storage",
    "azurerm_storage_container": "object_storage",
    "aws_instance": "compute",
    "google_compute_instance": "compute",
    "azurerm_virtual_machine": "compute",
    "azurerm_linux_virtual_machine": "compute",
    "aws_db_instance": "database",
    "aws_rds_cluster_instance": "database",
    "google_sql_database_instance": "database",
    "azurerm_postgresql_flexible_server": "database",
    "azurerm_mssql_database": "database",
    # Serverless
    "aws_lambda_function": "serverless",
    "google_cloudfunctions_function": "serverless",
    "google_cloudfunctions2_function": "serverless",
    "azurerm_function_app": "serverless",
    "azurerm_linux_function_app": "serverless",
}

# Maps Terraform resource types → cloudslayer provider name (for "current" highlighting)
RESOURCE_PROVIDER_MAP: dict[str, str] = {
    "aws_s3_bucket": "aws_s3",
    "aws_s3_bucket_v2": "aws_s3",
    "google_storage_bucket": "gcp_storage",
    "azurerm_storage_account": "azure_blob",
    "azurerm_storage_container": "azure_blob",
    "aws_instance": "aws_ec2",
    "google_compute_instance": "gcp_gce",
    "azurerm_virtual_machine": "azure_vm",
    "azurerm_linux_virtual_machine": "azure_vm",
    "aws_db_instance": "aws_rds",
    "aws_rds_cluster_instance": "aws_rds",
    "google_sql_database_instance": "gcp_cloudsql",
    "azurerm_postgresql_flexible_server": "azure_db",
    "azurerm_mssql_database": "azure_db",
    # Serverless
    "aws_lambda_function": "aws_lambda",
    "google_cloudfunctions_function": "gcp_functions",
    "google_cloudfunctions2_function": "gcp_functions",
    "azurerm_function_app": "azure_functions",
    "azurerm_linux_function_app": "azure_functions",
}

# Cost-relevant resource types cloudslayer detects but cannot price yet.
# Listed in output so users know the estimate is incomplete.
UNCOSTED_RESOURCES: dict[str, str] = {
    # AWS
    "aws_nat_gateway": "NAT Gateway (~$32.85/mo + $0.045/GB processed)",
    "aws_ebs_volume": "EBS volume",
    "aws_lb": "Application/Network Load Balancer",
    "aws_alb": "Application Load Balancer",
    "aws_elb": "Classic Load Balancer",
    "aws_eks_cluster": "EKS control plane (~$73/mo)",
    "aws_eks_node_group": "EKS node group",
    "aws_elasticache_cluster": "ElastiCache cluster",
    "aws_elasticache_replication_group": "ElastiCache replication group",
    "aws_dynamodb_table": "DynamoDB table",
    "aws_cloudfront_distribution": "CloudFront distribution",
    # GCP
    "google_container_cluster": "GKE cluster",
    "google_container_node_pool": "GKE node pool",
    "google_compute_disk": "Persistent disk",
    "google_compute_router_nat": "Cloud NAT",
    "google_redis_instance": "Memorystore for Redis",
    # Azure
    "azurerm_kubernetes_cluster": "AKS cluster",
    "azurerm_managed_disk": "Managed disk",
    "azurerm_lb": "Azure Load Balancer",
    "azurerm_nat_gateway": "Azure NAT Gateway",
    "azurerm_redis_cache": "Azure Cache for Redis",
}

# Known AWS instance type specs: name → (vcpu, memory_gb)
AWS_INSTANCE_SPECS: dict[str, tuple[int, float]] = {
    # t3
    "t3.nano": (2, 0.5),
    "t3.micro": (2, 1.0),
    "t3.small": (2, 2.0),
    "t3.medium": (2, 4.0),
    "t3.large": (2, 8.0),
    "t3.xlarge": (4, 16.0),
    "t3.2xlarge": (8, 32.0),
    # t4g
    "t4g.nano": (2, 0.5),
    "t4g.micro": (2, 1.0),
    "t4g.small": (2, 2.0),
    "t4g.medium": (2, 4.0),
    "t4g.large": (2, 8.0),
    "t4g.xlarge": (4, 16.0),
    "t4g.2xlarge": (8, 32.0),
    # m5
    "m5.large": (2, 8.0),
    "m5.xlarge": (4, 16.0),
    "m5.2xlarge": (8, 32.0),
    "m5.4xlarge": (16, 64.0),
    # m6i
    "m6i.large": (2, 8.0),
    "m6i.xlarge": (4, 16.0),
    "m6i.2xlarge": (8, 32.0),
    "m6i.4xlarge": (16, 64.0),
    # m6g
    "m6g.large": (2, 8.0),
    "m6g.xlarge": (4, 16.0),
    "m6g.2xlarge": (8, 32.0),
    "m6g.4xlarge": (16, 64.0),
    # c5
    "c5.large": (2, 4.0),
    "c5.xlarge": (4, 8.0),
    "c5.2xlarge": (8, 16.0),
    "c5.4xlarge": (16, 32.0),
    # c6i
    "c6i.large": (2, 4.0),
    "c6i.xlarge": (4, 8.0),
    "c6i.2xlarge": (8, 16.0),
    "c6i.4xlarge": (16, 32.0),
    # c6g
    "c6g.large": (2, 4.0),
    "c6g.xlarge": (4, 8.0),
    "c6g.2xlarge": (8, 16.0),
    "c6g.4xlarge": (16, 32.0),
    # r5
    "r5.large": (2, 16.0),
    "r5.xlarge": (4, 32.0),
    "r5.2xlarge": (8, 64.0),
    "r5.4xlarge": (16, 128.0),
    # r6i
    "r6i.large": (2, 16.0),
    "r6i.xlarge": (4, 32.0),
    "r6i.2xlarge": (8, 64.0),
    "r6i.4xlarge": (16, 128.0),
    # r6g
    "r6g.large": (2, 16.0),
    "r6g.xlarge": (4, 32.0),
    "r6g.2xlarge": (8, 64.0),
    # i3
    "i3.large": (2, 15.25),
    "i3.xlarge": (4, 30.5),
    "i3.2xlarge": (8, 61.0),
    # g4dn
    "g4dn.xlarge": (4, 16.0),
    "g4dn.2xlarge": (8, 32.0),
}

# GCP machine type specs: name → (vcpu, memory_gb)
GCP_INSTANCE_SPECS: dict[str, tuple[int, float]] = {
    "e2-micro": (2, 1.0),
    "e2-small": (2, 2.0),
    "e2-medium": (2, 4.0),
    "e2-standard-2": (2, 8.0),
    "e2-standard-4": (4, 16.0),
    "e2-standard-8": (8, 32.0),
    "e2-standard-16": (16, 64.0),
    "e2-standard-32": (32, 128.0),
    "n1-standard-1": (1, 3.75),
    "n1-standard-2": (2, 7.5),
    "n1-standard-4": (4, 15.0),
    "n1-standard-8": (8, 30.0),
    "n1-highcpu-4": (4, 3.6),
    "n1-highcpu-8": (8, 7.2),
    "n1-highmem-2": (2, 13.0),
    "n1-highmem-4": (4, 26.0),
    "n1-highmem-8": (8, 52.0),
    "n2-standard-2": (2, 8.0),
    "n2-standard-4": (4, 16.0),
    "n2-standard-8": (8, 32.0),
    "n2-standard-16": (16, 64.0),
    "n2-highmem-2": (2, 16.0),
    "n2-highmem-4": (4, 32.0),
    "n2-highmem-8": (8, 64.0),
    "n2-highmem-16": (16, 128.0),
    "c2-standard-4": (4, 16.0),
    "c2-standard-8": (8, 32.0),
    "c2-standard-16": (16, 64.0),
    "n2d-standard-2": (2, 8.0),
    "n2d-standard-4": (4, 16.0),
    "n2d-standard-8": (8, 32.0),
    "t2d-standard-1": (1, 4.0),
    "t2d-standard-2": (2, 8.0),
    "t2d-standard-4": (4, 16.0),
}

# Azure VM size specs: name → (vcpu, memory_gb)
AZURE_VM_SPECS: dict[str, tuple[int, float]] = {
    "Standard_B1s": (1, 1.0),
    "Standard_B1ms": (1, 2.0),
    "Standard_B2s": (2, 4.0),
    "Standard_B4ms": (4, 16.0),
    "Standard_B8ms": (8, 32.0),
    "Standard_B12ms": (12, 48.0),
    "Standard_D2s_v3": (2, 8.0),
    "Standard_D4s_v3": (4, 16.0),
    "Standard_D8s_v3": (8, 32.0),
    "Standard_D16s_v3": (16, 64.0),
    "Standard_D32s_v3": (32, 128.0),
    "Standard_D2s_v4": (2, 8.0),
    "Standard_D4s_v4": (4, 16.0),
    "Standard_D8s_v4": (8, 32.0),
    "Standard_D16s_v4": (16, 64.0),
    "Standard_D2as_v4": (2, 8.0),
    "Standard_D4as_v4": (4, 16.0),
    "Standard_D8as_v4": (8, 32.0),
    "Standard_E2s_v3": (2, 16.0),
    "Standard_E4s_v3": (4, 32.0),
    "Standard_E8s_v3": (8, 64.0),
    "Standard_E16s_v3": (16, 128.0),
    "Standard_E32s_v3": (32, 256.0),
    "Standard_F2s_v2": (2, 4.0),
    "Standard_F4s_v2": (4, 8.0),
    "Standard_F8s_v2": (8, 16.0),
    "Standard_F16s_v2": (16, 32.0),
    "Standard_F32s_v2": (32, 64.0),
    "Standard_L8s_v2": (8, 64.0),
    "Standard_L16s_v2": (16, 128.0),
}

AWS_RDS_SPECS: dict[str, tuple[int, float]] = {
    # db.t3
    "db.t3.micro": (2, 1.0),
    "db.t3.small": (2, 2.0),
    "db.t3.medium": (2, 4.0),
    "db.t3.large": (2, 8.0),
    "db.t3.xlarge": (4, 16.0),
    "db.t3.2xlarge": (8, 32.0),
    # db.t4g
    "db.t4g.micro": (2, 1.0),
    "db.t4g.small": (2, 2.0),
    "db.t4g.medium": (2, 4.0),
    "db.t4g.large": (2, 8.0),
    "db.t4g.xlarge": (4, 16.0),
    "db.t4g.2xlarge": (8, 32.0),
    # db.m5
    "db.m5.large": (2, 8.0),
    "db.m5.xlarge": (4, 16.0),
    "db.m5.2xlarge": (8, 32.0),
    # db.m6i
    "db.m6i.large": (2, 8.0),
    "db.m6i.xlarge": (4, 16.0),
    "db.m6i.2xlarge": (8, 32.0),
    "db.m6i.4xlarge": (16, 64.0),
    # db.m6g
    "db.m6g.large": (2, 8.0),
    "db.m6g.xlarge": (4, 16.0),
    "db.m6g.2xlarge": (8, 32.0),
    # db.r5
    "db.r5.large": (2, 16.0),
    "db.r5.xlarge": (4, 32.0),
    "db.r5.2xlarge": (8, 64.0),
    # db.r6i
    "db.r6i.large": (2, 16.0),
    "db.r6i.xlarge": (4, 32.0),
    "db.r6i.2xlarge": (8, 64.0),
    # db.r6g
    "db.r6g.large": (2, 16.0),
    "db.r6g.xlarge": (4, 32.0),
    "db.r6g.2xlarge": (8, 64.0),
}


@dataclass
class DetectedResource:
    terraform_type: str
    resource_name: str
    cloudslayer_type: str
    source_file: str
    attrs: dict
    current_provider: str = ""  # cloudslayer provider name for this resource
    instance_label: str = ""  # human-readable instance type (e.g. "t3.medium")


@dataclass
class UncostedResource:
    terraform_type: str
    resource_name: str
    source_file: str
    label: str  # human-readable description from UNCOSTED_RESOURCES


@dataclass
class ScanReport:
    supported: list[DetectedResource] = field(default_factory=list)
    uncosted: list[UncostedResource] = field(default_factory=list)
    other_count: int = 0  # resources with no direct cost (IAM, SGs, DNS, ...)

    @property
    def total_seen(self) -> int:
        return len(self.supported) + len(self.uncosted) + self.other_count


def scan(directory: str) -> list[DetectedResource]:
    """Back-compat wrapper: costed resources only. Prefer scan_path() for full reports."""
    return scan_path(directory).supported


def scan_path(path: str) -> ScanReport:
    """Scan a Terraform directory, a single .tf file, or a `terraform show -json` plan file."""
    p = Path(path)
    if p.is_file() and p.suffix == ".json":
        return _scan_plan_json(p)
    if p.is_file():
        return _scan_hcl_files([str(p)], p.parent.resolve())
    tf_files = glob.glob(f"{path}/**/*.tf", recursive=True)
    if not tf_files:
        tf_files = glob.glob(f"{path}/*.tf")
    return _scan_hcl_files(sorted(tf_files), Path(path).resolve())


def _classify(resource_type: str, name: str, attrs: dict, source: str, report: ScanReport) -> None:
    if resource_type in RESOURCE_MAP:
        report.supported.append(
            DetectedResource(
                terraform_type=resource_type,
                resource_name=name,
                cloudslayer_type=RESOURCE_MAP[resource_type],
                source_file=source,
                attrs=attrs,
                current_provider=RESOURCE_PROVIDER_MAP.get(resource_type, ""),
                instance_label=_extract_instance_label(resource_type, attrs),
            )
        )
    elif resource_type in UNCOSTED_RESOURCES:
        report.uncosted.append(
            UncostedResource(
                terraform_type=resource_type,
                resource_name=name,
                source_file=source,
                label=UNCOSTED_RESOURCES[resource_type],
            )
        )
    else:
        report.other_count += 1


def _scan_hcl_files(tf_files: list[str], base: Path) -> ScanReport:
    report = ScanReport()

    for tf_file in tf_files:
        try:
            with open(tf_file) as f:
                config = hcl2.load(f)
        except Exception:
            continue

        try:
            rel_path = str(Path(tf_file).resolve().relative_to(base))
        except ValueError:
            rel_path = tf_file

        for resource_block in config.get("resource", []):
            for resource_type_raw, instances in resource_block.items():
                resource_type = resource_type_raw.strip('"')
                for name_raw, attrs_raw in instances.items():
                    attrs = attrs_raw[0] if isinstance(attrs_raw, list) else attrs_raw
                    _classify(resource_type, name_raw.strip('"'), attrs, rel_path, report)

    return report


def _scan_plan_json(path: Path) -> ScanReport:
    """Parse `terraform show -json plan.out` (or state) output.

    Reads planned_values (plans) or values (state) — resources arrive fully
    resolved, so modules, variables, count and for_each are all expanded.
    """
    report = ScanReport()
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return report

    root = (
        data.get("planned_values", {}).get("root_module")
        or data.get("values", {}).get("root_module")
        or {}
    )

    def walk(module: dict) -> None:
        for res in module.get("resources", []):
            if res.get("mode", "managed") != "managed":
                continue
            name = res.get("name", "")
            if res.get("index") is not None:
                name = f"{name}-{res['index']}"
            _classify(res.get("type", ""), name, res.get("values") or {}, path.name, report)
        for child in module.get("child_modules", []):
            walk(child)

    walk(root)
    return report


def _extract_instance_label(resource_type: str, attrs: dict) -> str:
    """Extract the human-readable instance type/size from attrs."""
    raw = ""
    if "aws_instance" in resource_type:
        raw = attrs.get("instance_type", "")
    elif "google_compute" in resource_type:
        raw = attrs.get("machine_type", "")
    elif "azurerm" in resource_type and "database" not in resource_type:
        raw = attrs.get("size", attrs.get("vm_size", ""))
    elif "aws_db_instance" in resource_type or "aws_rds" in resource_type:
        raw = attrs.get("instance_class", "")
    elif "google_sql" in resource_type:
        raw = attrs.get("tier", "")

    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw).strip('"')


def build_specs_from_resources(
    resources: list[DetectedResource],
) -> tuple[
    list[tuple[ObjectStorageSpec, str]],  # (spec, current_provider)
    list[tuple[ComputeSpec, str, str]],  # (spec, current_provider, instance_label)
    list[tuple[DatabaseSpec, str, str]],  # (spec, current_provider, instance_label)
    list[tuple[ServerlessSpec, str]],  # (spec, current_provider)
]:
    """Build typed specs from detected Terraform resources, preserving current-provider info."""
    storage: list[tuple[ObjectStorageSpec, str]] = []
    compute: list[tuple[ComputeSpec, str, str]] = []
    database: list[tuple[DatabaseSpec, str, str]] = []
    serverless: list[tuple[ServerlessSpec, str]] = []

    for r in resources:
        slug = r.resource_name.replace("_", "-")

        if r.cloudslayer_type == "object_storage":
            # Usage numbers not available from Terraform — use sensible defaults
            spec = ObjectStorageSpec(
                name=slug,
                storage_gb=100,
                get_requests=1_000_000,
                put_requests=100_000,
                egress_gb=50,
            )
            storage.append((spec, r.current_provider))

        elif r.cloudslayer_type == "compute":
            itype = r.instance_label
            vcpu, mem = (
                AWS_INSTANCE_SPECS.get(itype)
                or GCP_INSTANCE_SPECS.get(itype)
                or AZURE_VM_SPECS.get(itype)
                or (2, 4.0)
            )
            spec = ComputeSpec(name=slug, vcpu=vcpu, memory_gb=mem)
            compute.append((spec, r.current_provider, itype))

        elif r.cloudslayer_type == "database":
            iclass = r.instance_label
            storage_raw = r.attrs.get("allocated_storage", 20)
            if isinstance(storage_raw, list):
                storage_raw = storage_raw[0] if storage_raw else 20
            storage_gb = float(storage_raw)
            vcpu, mem = AWS_RDS_SPECS.get(iclass, (2, 4.0))
            engine_raw = r.attrs.get("engine", "postgres")
            if isinstance(engine_raw, list):
                engine_raw = engine_raw[0] if engine_raw else "postgres"
            engine = str(engine_raw).strip('"')
            spec = DatabaseSpec(
                name=slug, vcpu=vcpu, memory_gb=mem, storage_gb=storage_gb, engine=engine
            )
            database.append((spec, r.current_provider, iclass))

        elif r.cloudslayer_type == "serverless":
            memory_raw = r.attrs.get("memory_size", r.attrs.get("available_memory_mb", 256))
            if isinstance(memory_raw, list):
                memory_raw = memory_raw[0] if memory_raw else 256
            memory_mb = int(memory_raw) if memory_raw else 256
            spec = ServerlessSpec(name=slug, memory_mb=memory_mb)
            serverless.append((spec, r.current_provider))

    return storage, compute, database, serverless


def generate_spec(resources: list[DetectedResource]) -> str:
    """Generate a cloudslayer .hcl spec from detected Terraform resources."""
    lines: list[str] = [
        "# Generated by cloudslayer scan",
        "# Fill in your actual usage numbers below",
        "",
    ]

    for r in resources:
        lines.append(f"# Source: {r.source_file} — {r.terraform_type}.{r.resource_name}")

        if r.cloudslayer_type == "object_storage":
            slug = r.resource_name.replace("_", "-")
            lines += [
                f'object_storage "{slug}" {{',
                "  storage_gb   = 100    # FIXME: actual GB stored/month",
                "  get_requests = 1000000",
                "  put_requests = 100000",
                "  egress_gb    = 50",
                "}",
                "",
            ]

        elif r.cloudslayer_type == "compute":
            slug = r.resource_name.replace("_", "-")
            itype = r.instance_label
            vcpu, mem = (
                AWS_INSTANCE_SPECS.get(itype)
                or GCP_INSTANCE_SPECS.get(itype)
                or AZURE_VM_SPECS.get(itype)
                or (2, 4.0)
            )
            lines += [
                f'compute "{slug}" {{',
                f"  vcpu      = {vcpu}  # from {itype}" if itype else f"  vcpu      = {vcpu}",
                f"  memory_gb = {mem}",
                "}",
                "",
            ]

        elif r.cloudslayer_type == "database":
            slug = r.resource_name.replace("_", "-")
            iclass = r.instance_label
            storage = r.attrs.get("allocated_storage", 20)
            if isinstance(storage, list):
                storage = storage[0] if storage else 20
            vcpu, mem = AWS_RDS_SPECS.get(iclass, (2, 4.0))
            lines += [
                f'database "{slug}" {{',
                f"  vcpu       = {vcpu}  # from {iclass}" if iclass else f"  vcpu       = {vcpu}",
                f"  memory_gb  = {mem}",
                f"  storage_gb = {storage}",
                "}",
                "",
            ]

        elif r.cloudslayer_type == "serverless":
            slug = r.resource_name.replace("_", "-")
            memory_raw = r.attrs.get("memory_size", r.attrs.get("available_memory_mb", 256))
            if isinstance(memory_raw, list):
                memory_raw = memory_raw[0] if memory_raw else 256
            memory_mb = int(memory_raw) if memory_raw else 256
            lines += [
                f'serverless "{slug}" {{',
                "  invocations_per_month = 1000000  # FIXME: actual invocations/month",
                "  avg_duration_ms       = 100",
                f"  memory_mb             = {memory_mb}",
                "}",
                "",
            ]

    return "\n".join(lines)
