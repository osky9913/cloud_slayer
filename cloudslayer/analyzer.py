"""Load AnalysisResource lists from Terraform files or live cloud billing."""

from __future__ import annotations

from .strategies import AnalysisResource


def load_from_terraform(path: str) -> list[AnalysisResource]:
    """Scan Terraform files and price each resource at its current provider."""
    resources, _report = load_from_terraform_detailed(path)
    return resources


def load_from_terraform_detailed(path: str):
    """Like load_from_terraform, but also returns the ScanReport (uncosted resources etc.)."""
    from .engine import plan_compute, plan_database, plan_object_storage, plan_serverless
    from .scanner import build_specs_from_resources, scan_path

    report = scan_path(path)
    detected = report.supported
    if not detected:
        return [], report

    storage_triples, compute_triples, database_triples, serverless_triples = (
        build_specs_from_resources(detected)
    )
    result: list[AnalysisResource] = []

    for spec, provider, label in compute_triples:
        priced = _result_for_provider(plan_compute(spec, provider, label), provider)
        if priced is None:
            continue
        result.append(
            AnalysisResource(
                name=spec.name,
                service="compute",
                current_provider=provider,
                monthly_cost=priced.total,
                compute_spec=spec,
                instance_type=label,
                price_source=priced.price_source,
                source_url=priced.source_url,
            )
        )

    for spec, provider in storage_triples:
        priced = _result_for_provider(plan_object_storage(spec), provider)
        if priced is None:
            continue
        result.append(
            AnalysisResource(
                name=spec.name,
                service="storage",
                current_provider=provider,
                monthly_cost=priced.total,
                storage_spec=spec,
                price_source=priced.price_source,
                source_url=priced.source_url,
            )
        )

    for spec, provider, label in database_triples:
        priced = _result_for_provider(plan_database(spec, provider, label), provider)
        if priced is None:
            continue
        result.append(
            AnalysisResource(
                name=spec.name,
                service="database",
                current_provider=provider,
                monthly_cost=priced.total,
                database_spec=spec,
                instance_type=label,
                price_source=priced.price_source,
                source_url=priced.source_url,
            )
        )

    for spec, provider in serverless_triples:
        priced = _result_for_provider(plan_serverless(spec), provider)
        if priced is None:
            continue
        result.append(
            AnalysisResource(
                name=spec.name,
                service="serverless",
                current_provider=provider,
                monthly_cost=priced.total,
                price_source=priced.price_source,
                source_url=priced.source_url,
            )
        )

    return result, report


def load_from_cloud(connector, days: int = 30) -> list[AnalysisResource]:
    """Convert any cloud connector's resource list to AnalysisResource.

    Works with AWSActualResource, GCPActualResource, AzureActualResource —
    they all share the same field names.
    """
    raw = connector.get_spend(days)
    source = (
        "actual billing"
        if connector.__class__.__name__ == "AWSConnector"
        else "live catalog estimate"
    )
    return [
        AnalysisResource(
            name=r.display_name,
            service=r.service,
            current_provider=r.current_provider,
            monthly_cost=r.actual_monthly_cost,
            compute_spec=r.compute_spec,
            storage_spec=r.storage_spec,
            database_spec=r.database_spec,
            instance_type=r.instance_type,
            price_source=getattr(r, "price_source", source),
            source_url=getattr(r, "source_url", ""),
        )
        for r in raw
    ]


# Keep old name as alias for backward compatibility
load_from_aws = load_from_cloud


def _result_for_provider(results: list, provider: str):
    return next((result for result in results if result.provider == provider), None)
