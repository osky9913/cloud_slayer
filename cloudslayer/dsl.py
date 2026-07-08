from __future__ import annotations

import hcl2

from .models import ComputeSpec, DatabaseSpec, ObjectStorageSpec, ServerlessSpec


def parse_hcl(
    file_path: str,
) -> tuple[list[ObjectStorageSpec], list[ComputeSpec], list[DatabaseSpec]]:
    """Parse an HCL spec file. Returns (storage_specs, compute_specs, database_specs).

    For serverless support, use parse_hcl_full() which returns a 4-tuple.
    """
    storage_specs, compute_specs, database_specs, _ = parse_hcl_full(file_path)
    return storage_specs, compute_specs, database_specs


def parse_hcl_full(
    file_path: str,
) -> tuple[list[ObjectStorageSpec], list[ComputeSpec], list[DatabaseSpec], list[ServerlessSpec]]:
    """Parse an HCL spec file including serverless blocks.

    Returns (storage_specs, compute_specs, database_specs, serverless_specs).
    """
    with open(file_path) as f:
        config = hcl2.load(f)

    storage_specs: list[ObjectStorageSpec] = []
    compute_specs: list[ComputeSpec] = []
    database_specs: list[DatabaseSpec] = []
    serverless_specs: list[ServerlessSpec] = []

    for block in config.get("object_storage", []):
        for name, attrs_raw in block.items():
            attrs = attrs_raw[0] if isinstance(attrs_raw, list) else attrs_raw
            storage_specs.append(
                ObjectStorageSpec(
                    name=name.strip('"'),
                    storage_gb=float(attrs.get("storage_gb", 0)),
                    get_requests=int(attrs.get("get_requests", 0)),
                    put_requests=int(attrs.get("put_requests", 0)),
                    egress_gb=float(attrs.get("egress_gb", 0.0)),
                )
            )

    for block in config.get("compute", []):
        for name, attrs_raw in block.items():
            attrs = attrs_raw[0] if isinstance(attrs_raw, list) else attrs_raw
            compute_specs.append(
                ComputeSpec(
                    name=name.strip('"'),
                    vcpu=int(attrs.get("vcpu", 1)),
                    memory_gb=float(attrs.get("memory_gb", 1.0)),
                )
            )

    for block in config.get("database", []):
        for name, attrs_raw in block.items():
            attrs = attrs_raw[0] if isinstance(attrs_raw, list) else attrs_raw
            database_specs.append(
                DatabaseSpec(
                    name=name.strip('"'),
                    vcpu=int(attrs.get("vcpu", 1)),
                    memory_gb=float(attrs.get("memory_gb", 1.0)),
                    storage_gb=float(attrs.get("storage_gb", 20.0)),
                    engine=str(attrs.get("engine", "postgres")).strip('"'),
                )
            )

    for block in config.get("serverless", []):
        for name, attrs_raw in block.items():
            attrs = attrs_raw[0] if isinstance(attrs_raw, list) else attrs_raw
            serverless_specs.append(
                ServerlessSpec(
                    name=name.strip('"'),
                    invocations_per_month=int(attrs.get("invocations_per_month", 1_000_000)),
                    avg_duration_ms=float(attrs.get("avg_duration_ms", 100.0)),
                    memory_mb=int(attrs.get("memory_mb", 256)),
                )
            )

    return storage_specs, compute_specs, database_specs, serverless_specs
