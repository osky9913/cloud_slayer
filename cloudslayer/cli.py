from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer

from .console import error_console
from .dsl import parse_hcl_full
from .engine import plan_compute, plan_database, plan_object_storage
from .renderer import (
    console,
    render_analyze,
    render_compute_comparison,
    render_database_comparison,
    render_diff,
    render_header,
    render_serverless_comparison,
    render_storage_comparison,
    render_total_summary,
)

app = typer.Typer(
    help="cloudslayer — compare cloud costs before you deploy",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

_FALLBACK_HELP = (
    "Allow verified static AWS/Azure prices when live or cached pricing is unavailable. "
    "GCP never uses hard-coded prices."
)
_LIVE_HELP = "Force live pricing API calls and bypass local cache reads for this run."


def _configure_pricing(fallback: bool, live: bool) -> None:
    from .config import set_fallback_prices, set_force_live_prices
    from .pricing import clear_pricing_warnings

    set_fallback_prices(fallback)
    set_force_live_prices(live)
    clear_pricing_warnings()


def _render_pricing_warnings() -> None:
    from .pricing import pricing_warnings

    warnings = pricing_warnings()
    if not warnings:
        return
    error_console.print("\n[yellow bold]Pricing unavailable[/yellow bold]")
    for provider, detail in warnings:
        error_console.print(f"  [yellow]•[/yellow] [bold]{provider}[/bold]: {detail}")
    error_console.print()


def _render_coverage(report, costed_count: int | None = None) -> None:
    """Honesty line: how much of the detected infrastructure is actually costed."""
    costed = len(report.supported) if costed_count is None else costed_count
    unavailable = max(0, len(report.supported) - costed)
    if not report.uncosted and report.other_count == 0 and unavailable == 0:
        return
    parts = [f"[bold]{costed}[/bold] costed"]
    if unavailable:
        parts.append(f"[yellow]{unavailable} supported but pricing unavailable[/yellow]")
    if report.uncosted:
        parts.append(f"[yellow]{len(report.uncosted)} detected but not costed yet[/yellow]")
    if report.other_count:
        parts.append(
            f"[dim]{report.other_count} with no direct cost (IAM, networking, DNS, ...)[/dim]"
        )
    console.print(f"  Resources: {' · '.join(parts)}")
    for u in report.uncosted:
        console.print(
            f"    [yellow]•[/yellow] [cyan]{u.terraform_type}[/cyan].{u.resource_name}  [dim]{u.label}[/dim]"
        )
    if report.uncosted:
        console.print("  [dim]Cost estimates below exclude these resources.[/dim]")
    console.print()


def _filter_results(results: list, provider_filter: str) -> list:
    if not provider_filter:
        return results
    filters = {f.strip().lower() for f in provider_filter.split(",")}
    return [
        r
        for r in results
        if any(f in r.provider.lower() or f in r.display_name.lower() for f in filters)
    ]


@app.command()
def plan(
    file: Path = typer.Argument(..., help="Path to .hcl infrastructure spec"),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: [bold]table[/bold] | [bold]json[/bold] | [bold]markdown[/bold] (GitHub-flavored, for PR comments)",
    ),
    top: int = typer.Option(0, "--top", "-n", help="Show only the N cheapest providers (0 = all)"),
    provider: str = typer.Option(
        "", "--provider", "-p", help="Filter providers by name, comma-separated (e.g. aws,gcp)"
    ),
    fail_if_over: float = typer.Option(
        0.0, "--fail-if-over", help="Exit 2 if cheapest monthly total exceeds this amount (for CI)"
    ),
    region: str = typer.Option(
        "",
        "--region",
        "-r",
        help="Cloud region (e.g. us-east-1, eu-west-1, ap-southeast-1). Default: us-east-1",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Launch interactive split-panel TUI"
    ),
    fallback: bool = typer.Option(False, "--fallback", help=_FALLBACK_HELP),
    live: bool = typer.Option(False, "--live", help=_LIVE_HELP),
) -> None:
    """Estimate and compare costs across providers for every resource in your spec."""
    _configure_pricing(fallback, live)
    if region:
        from .config import set_region

        set_region(region)

    if not file.exists() or not file.is_file():
        console.print(f"[red]Not a file:[/red] {file}")
        raise typer.Exit(1)

    storage_specs, compute_specs, database_specs, serverless_specs = parse_hcl_full(str(file))

    if not storage_specs and not compute_specs and not database_specs and not serverless_specs:
        console.print("[yellow]No resources found in spec file.[/yellow]")
        raise typer.Exit(1)

    from .engine import plan_serverless
    from .renderer import render_serverless_comparison

    with console.status("[bold cyan]Fetching pricing data...[/bold cyan]", spinner="dots"):
        all_storage = [
            (s, _filter_results(plan_object_storage(s), provider)) for s in storage_specs
        ]
        all_compute = [(s, _filter_results(plan_compute(s), provider)) for s in compute_specs]
        all_database = [(s, _filter_results(plan_database(s), provider)) for s in database_specs]
        all_serverless = [(s, plan_serverless(s)) for s in serverless_specs]
    if not interactive:
        _render_pricing_warnings()

    # Remove resources with no results after filtering
    all_storage = [(s, rs) for s, rs in all_storage if rs]
    all_compute = [(s, rs) for s, rs in all_compute if rs]
    all_database = [(s, rs) for s, rs in all_database if rs]
    all_serverless = [(s, rs) for s, rs in all_serverless if rs]

    if not all_storage and not all_compute and not all_database and not all_serverless:
        if interactive:
            _render_pricing_warnings()
        console.print(
            f"[yellow]No results after filtering for provider=[bold]{provider}[/bold][/yellow]"
        )
        raise typer.Exit(1)

    if format == "json":
        _output_json(all_storage, all_compute, all_database, all_serverless)
        _check_budget(all_storage, all_compute, all_database, fail_if_over, all_serverless)
        return

    if format in ("markdown", "github"):
        _output_markdown(all_storage, all_compute, all_database, all_serverless, top=top)
        _check_budget(all_storage, all_compute, all_database, fail_if_over, all_serverless)
        return

    if interactive:
        from .pricing import pricing_warnings
        from .tui import PlanTUI

        cheapest_total = (
            sum(min(r.total for r in rs) for _, rs in all_storage if rs)
            + sum(min(r.total for r in rs) for _, rs in all_compute if rs)
            + sum(min(r.total for r in rs) for _, rs in all_database if rs)
            + sum(min(r.monthly_cost for r in rs) for _, rs in all_serverless if rs)
        )
        PlanTUI(
            all_storage,
            all_compute,
            all_database,
            all_serverless,
            title=f"cloudslayer plan  ·  {file.name}  ·  ${cheapest_total:,.2f}/mo cheapest",
            pricing_warnings=pricing_warnings(),
        ).run()
        _check_budget(all_storage, all_compute, all_database, fail_if_over, all_serverless)
        return

    render_header()
    for spec, results in all_storage:
        render_storage_comparison(spec, results, top=top)
    for spec, results in all_compute:
        render_compute_comparison(spec, results, top=top)
    for spec, results in all_database:
        render_database_comparison(spec, results, top=top)
    for spec, results in all_serverless:
        render_serverless_comparison(spec, results, top=top)

    total_resources = len(all_storage) + len(all_compute) + len(all_database) + len(all_serverless)
    if total_resources > 1:
        render_total_summary(all_storage, all_compute, all_database, all_serverless)

    _check_budget(all_storage, all_compute, all_database, fail_if_over, all_serverless)


def _check_budget(
    all_storage: list,
    all_compute: list,
    all_database: list,
    fail_if_over: float,
    all_serverless: list | None = None,
) -> None:
    if fail_if_over <= 0:
        return
    cheapest_total = (
        sum(min(r.total for r in rs) for _, rs in all_storage if rs)
        + sum(min(r.total for r in rs) for _, rs in all_compute if rs)
        + sum(min(r.total for r in rs) for _, rs in all_database if rs)
        + sum(min(r.monthly_cost for r in rs) for _, rs in (all_serverless or []) if rs)
    )
    if cheapest_total > fail_if_over:
        console.print(
            f"\n[red bold]BUDGET EXCEEDED:[/red bold] cheapest combination "
            f"[bold]${cheapest_total:,.2f}/mo[/bold] exceeds limit of [bold]${fail_if_over:,.2f}/mo[/bold]"
        )
        raise typer.Exit(2)


def _output_json(
    all_storage: list, all_compute: list, all_database: list, all_serverless: list | None = None
) -> None:
    output: dict = {"object_storage": [], "compute": [], "database": [], "serverless": []}

    for spec, results in all_storage:
        output["object_storage"].append(
            {
                "name": spec.name,
                "usage": asdict(spec),
                "results": [
                    {
                        "provider": r.provider,
                        "display_name": r.display_name,
                        "storage_cost": round(r.storage_cost, 4),
                        "request_cost": round(r.request_cost, 4),
                        "egress_cost": round(r.egress_cost, 4),
                        "total": round(r.total, 4),
                        "annual": round(r.total * 12, 2),
                        "price_source": r.price_source,
                        "source_url": r.source_url,
                    }
                    for r in results
                ],
            }
        )

    for spec, results in all_compute:
        output["compute"].append(
            {
                "name": spec.name,
                "spec": asdict(spec),
                "results": [
                    {
                        "provider": r.provider,
                        "display_name": r.display_name,
                        "instance": r.instance_name,
                        "instance_vcpu": r.instance_vcpu,
                        "instance_memory_gb": r.instance_memory_gb,
                        "total": round(r.price_per_month, 2),
                        "annual": round(r.price_per_month * 12, 2),
                        "price_source": r.price_source,
                        "source_url": r.source_url,
                    }
                    for r in results
                ],
            }
        )

    for spec, results in all_database:
        output["database"].append(
            {
                "name": spec.name,
                "spec": asdict(spec),
                "results": [
                    {
                        "provider": r.provider,
                        "display_name": r.display_name,
                        "plan": r.plan_name,
                        "plan_vcpu": r.plan_vcpu,
                        "plan_memory_gb": r.plan_memory_gb,
                        "instance_cost": round(r.instance_cost, 2),
                        "storage_cost": round(r.storage_cost, 2),
                        "total": round(r.total, 2),
                        "annual": round(r.total * 12, 2),
                        "price_source": r.price_source,
                        "source_url": r.source_url,
                    }
                    for r in results
                ],
            }
        )

    for spec, results in all_serverless or []:
        output["serverless"].append(
            {
                "name": spec.name,
                "spec": {
                    "invocations_per_month": spec.invocations_per_month,
                    "avg_duration_ms": spec.avg_duration_ms,
                    "memory_mb": spec.memory_mb,
                },
                "results": [
                    {
                        "provider": r.provider,
                        "display_name": r.display_name,
                        "monthly_cost": round(r.monthly_cost, 4),
                        "per_million_requests": round(r.per_million_requests, 4),
                        "notes": r.notes,
                        "price_source": r.price_source,
                        "source_url": r.source_url,
                    }
                    for r in results
                ],
            }
        )

    print(json.dumps(output, indent=2))


def _output_markdown(
    all_storage: list,
    all_compute: list,
    all_database: list,
    all_serverless: list | None = None,
    top: int = 0,
) -> None:
    """GitHub-flavored markdown output — designed for PR comments."""
    lines: list[str] = ["## ☁️ cloudslayer — cloud cost comparison", ""]

    def cap(results: list) -> list:
        return results[:top] if top > 0 else results

    for spec, results in all_compute:
        lines.append(f'### `compute "{spec.name}"` — {spec.vcpu} vCPU · {spec.memory_gb:g} GB RAM')
        lines.append("")
        lines.append("| Provider | Instance | Monthly | Annual | Source |")
        lines.append("|---|---|--:|--:|---|")
        for i, r in enumerate(cap(results)):
            star = "⭐ " if i == 0 else ""
            lines.append(
                f"| {star}{r.display_name} | `{r.instance_name}` | ${r.total:,.2f} | "
                f"${r.total * 12:,.2f} | {r.price_source} |"
            )
        lines.append("")

    for spec, results in all_database:
        lines.append(
            f'### `database "{spec.name}"` — {spec.vcpu} vCPU · {spec.memory_gb:g} GB · {spec.storage_gb:g} GB storage'
        )
        lines.append("")
        lines.append("| Provider | Plan | Monthly | Annual | Source |")
        lines.append("|---|---|--:|--:|---|")
        for i, r in enumerate(cap(results)):
            star = "⭐ " if i == 0 else ""
            lines.append(
                f"| {star}{r.display_name} | `{r.plan_name}` | ${r.total:,.2f} | "
                f"${r.total * 12:,.2f} | {r.price_source} |"
            )
        lines.append("")

    for spec, results in all_storage:
        lines.append(
            f'### `object_storage "{spec.name}"` — {spec.storage_gb:g} GB · {spec.egress_gb:g} GB egress'
        )
        lines.append("")
        lines.append("| Provider | Storage | Egress | Monthly | Annual | Source |")
        lines.append("|---|--:|--:|--:|--:|---|")
        for i, r in enumerate(cap(results)):
            star = "⭐ " if i == 0 else ""
            lines.append(
                f"| {star}{r.display_name} | ${r.storage_cost:,.2f} | ${r.egress_cost:,.2f}"
                f" | ${r.total:,.2f} | ${r.total * 12:,.2f} | {r.price_source} |"
            )
        lines.append("")

    for spec, results in all_serverless or []:
        lines.append(
            f'### `serverless "{spec.name}"` — {spec.invocations_per_month:,} invocations · {spec.memory_mb} MB'
        )
        lines.append("")
        lines.append("| Provider | Monthly | Per 1M requests | Source |")
        lines.append("|---|--:|--:|---|")
        for i, r in enumerate(cap(results)):
            star = "⭐ " if i == 0 else ""
            lines.append(
                f"| {star}{r.display_name} | ${r.monthly_cost:,.4f} | "
                f"${r.per_million_requests:,.4f} | {r.price_source} |"
            )
        lines.append("")

    cheapest = (
        sum(min(r.total for r in rs) for _, rs in all_storage if rs)
        + sum(min(r.total for r in rs) for _, rs in all_compute if rs)
        + sum(min(r.total for r in rs) for _, rs in all_database if rs)
        + sum(min(r.monthly_cost for r in rs) for _, rs in (all_serverless or []) if rs)
    )
    lines.append(f"**Cheapest combination: ${cheapest:,.2f}/mo** (${cheapest * 12:,.0f}/yr)")
    lines.append("")
    lines.append(
        "> Prices are planning estimates. Generated by [cloudslayer](https://github.com/cloudslayer-dev/cloudslayer)."
    )

    print("\n".join(lines))


@app.command()
def compare(
    directory: Path = typer.Argument(
        Path("."), help="Terraform directory, .tf file, or `terraform show -json` plan file"
    ),
    top: int = typer.Option(
        0, "--top", "-n", help="Show only the N cheapest alternatives (0 = all)"
    ),
    provider: str = typer.Option("", "--provider", "-p", help="Filter providers (comma-separated)"),
    region: str = typer.Option(
        "",
        "--region",
        "-r",
        help="Cloud region (e.g. us-east-1, eu-west-1, ap-southeast-1). Default: us-east-1",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Launch interactive split-panel TUI"
    ),
    fallback: bool = typer.Option(False, "--fallback", help=_FALLBACK_HELP),
    live: bool = typer.Option(False, "--live", help=_LIVE_HELP),
) -> None:
    """Compare what you're paying NOW vs every alternative provider.

    Scans your Terraform files, detects your current cloud provider and instance
    types, and shows a ranked cost comparison with [yellow]your current cost highlighted[/yellow]
    and exact savings per alternative.

    Example:
      cloudslayer compare ./terraform/
      cloudslayer compare . --top 5
    """
    _configure_pricing(fallback, live)
    if region:
        from .config import set_region

        set_region(region)

    from rich.rule import Rule

    from .scanner import build_specs_from_resources, scan_path

    report = scan_path(str(directory))
    resources = report.supported
    if not resources:
        console.print(f"[yellow]No recognized cloud resources found in {directory}[/yellow]")
        raise typer.Exit(1)

    storage_triples, compute_triples, database_triples, serverless_triples = (
        build_specs_from_resources(resources)
    )

    if (
        not storage_triples
        and not compute_triples
        and not database_triples
        and not serverless_triples
    ):
        console.print("[yellow]No priceable resources found.[/yellow]")
        raise typer.Exit(1)

    from .engine import plan_serverless

    with console.status("[bold cyan]Fetching pricing data...[/bold cyan]", spinner="dots"):
        all_storage = [
            (spec, _filter_results(plan_object_storage(spec), provider), cur)
            for spec, cur in storage_triples
        ]
        all_compute = [
            (spec, _filter_results(plan_compute(spec, cur, label), provider), cur, label)
            for spec, cur, label in compute_triples
        ]
        all_database = [
            (spec, _filter_results(plan_database(spec, cur, label), provider), cur, label)
            for spec, cur, label in database_triples
        ]
        all_serverless_compare = [
            (spec, plan_serverless(spec), cur) for spec, cur in serverless_triples
        ]
    if not interactive:
        _render_pricing_warnings()

    if interactive:
        from .pricing import pricing_warnings
        from .tui import PlanTUI

        all_storage = [item for item in all_storage if item[1]]
        all_compute = [item for item in all_compute if item[1]]
        all_database = [item for item in all_database if item[1]]
        all_serverless_compare = [item for item in all_serverless_compare if item[1]]
        if not any((all_storage, all_compute, all_database, all_serverless_compare)):
            _render_pricing_warnings()
            console.print("[red]No priced resources are available for the interactive view.[/red]")
            raise typer.Exit(1)

        PlanTUI(
            all_storage,
            all_compute,
            all_database,
            all_serverless_compare,
            title=f"cloudslayer compare  ·  {directory}",
            pricing_warnings=pricing_warnings(),
        ).run()
        return

    console.print()
    console.print(
        Rule(
            f"[bold cyan]cloudslayer compare[/bold cyan]  current vs alternatives  [dim]({directory})[/dim]",
            style="cyan",
        )
    )
    console.print()
    current_costed = (
        sum(any(result.provider == cur for result in results) for _, results, cur in all_storage)
        + sum(
            any(result.provider == cur for result in results) for _, results, cur, _ in all_compute
        )
        + sum(
            any(result.provider == cur for result in results) for _, results, cur, _ in all_database
        )
        + sum(
            any(result.provider == cur for result in results)
            for _, results, cur in all_serverless_compare
        )
    )
    _render_coverage(report, current_costed)

    current_spend = 0.0
    cheapest_spend = 0.0

    for spec, results, cur in all_storage:
        if not results:
            continue
        render_storage_comparison(spec, results, top=top, current_provider=cur)
        current_r = next((r for r in results if r.provider == cur), None)
        current_spend += current_r.total if current_r else 0.0
        cheapest_spend += min(r.total for r in results)

    for spec, results, cur, label in all_compute:
        if not results:
            continue
        render_compute_comparison(
            spec, results, top=top, current_provider=cur, instance_label=label
        )
        current_r = next((r for r in results if r.provider == cur), None)
        current_spend += current_r.total if current_r else 0.0
        cheapest_spend += min(r.total for r in results)

    for spec, results, cur, label in all_database:
        if not results:
            continue
        render_database_comparison(
            spec, results, top=top, current_provider=cur, instance_label=label
        )
        current_r = next((r for r in results if r.provider == cur), None)
        current_spend += current_r.total if current_r else 0.0
        cheapest_spend += min(r.total for r in results)

    for spec, results, cur in all_serverless_compare:
        if not results:
            continue
        render_serverless_comparison(spec, results, top=top, current_provider=cur)
        current_r = next((r for r in results if r.provider == cur), None)
        current_spend += current_r.monthly_cost if current_r else 0.0
        cheapest_spend += min(r.monthly_cost for r in results)

    # Total savings banner
    if current_spend > 0:
        total_savings = current_spend - cheapest_spend
        console.print(Rule("[bold]Total Savings Opportunity[/bold]", style="cyan"))
        console.print()
        console.print(
            f"  Current monthly spend:   [yellow bold]${current_spend:,.2f}/mo[/yellow bold]  (${current_spend * 12:,.0f}/yr)"
        )
        console.print(
            f"  Cheapest combination:    [green bold]${cheapest_spend:,.2f}/mo[/green bold]  (${cheapest_spend * 12:,.0f}/yr)"
        )
        if total_savings > 0.01:
            console.print(
                f"\n  [bold]You could save[/bold] [green bold]${total_savings:,.2f}/mo[/green bold]"
                f" — that's [green bold]${total_savings * 12:,.0f}/yr[/green bold] by switching providers.\n"
            )
        else:
            console.print(
                "\n  [green]You're already on the cheapest available providers.[/green]\n"
            )


@app.command()
def diff(
    before: Path = typer.Argument(..., help="Previous .hcl spec (baseline)"),
    after: Path = typer.Argument(..., help="Updated .hcl spec to compare against baseline"),
    provider: str = typer.Option("", "--provider", "-p", help="Filter providers (comma-separated)"),
    fail_if_over: float = typer.Option(
        0.0, "--fail-if-over", help="Exit 2 if new cheapest total exceeds this amount"
    ),
    region: str = typer.Option(
        "",
        "--region",
        "-r",
        help="Cloud region (e.g. us-east-1, eu-west-1, ap-southeast-1). Default: us-east-1",
    ),
    fallback: bool = typer.Option(False, "--fallback", help=_FALLBACK_HELP),
    live: bool = typer.Option(False, "--live", help=_LIVE_HELP),
) -> None:
    """Show the cost delta between two infrastructure specs.

    Perfect for CI — run this on every PR that touches your infra spec
    to see exactly how much costs will change before you merge.

    Example:
      cloudslayer diff infra-before.hcl infra-after.hcl
    """
    _configure_pricing(fallback, live)
    if region:
        from .config import set_region

        set_region(region)

    for path, label in [(before, "before"), (after, "after")]:
        if not path.exists():
            console.print(f"[red]File not found ({label}):[/red] {path}")
            raise typer.Exit(1)

    with console.status("[bold cyan]Fetching pricing data...[/bold cyan]", spinner="dots"):
        b_storage, b_compute, b_database, _b_serverless = parse_hcl_full(str(before))
        a_storage, a_compute, a_database, _a_serverless = parse_hcl_full(str(after))

        before_storage = [(s, _filter_results(plan_object_storage(s), provider)) for s in b_storage]
        before_compute = [(s, _filter_results(plan_compute(s), provider)) for s in b_compute]
        before_database = [(s, _filter_results(plan_database(s), provider)) for s in b_database]

        after_storage = [(s, _filter_results(plan_object_storage(s), provider)) for s in a_storage]
        after_compute = [(s, _filter_results(plan_compute(s), provider)) for s in a_compute]
        after_database = [(s, _filter_results(plan_database(s), provider)) for s in a_database]
    _render_pricing_warnings()

    render_diff(
        before_storage,
        before_compute,
        before_database,
        after_storage,
        after_compute,
        after_database,
    )

    if fail_if_over > 0:
        _check_budget(after_storage, after_compute, after_database, fail_if_over)


def _make_connector(cloud: str, profile: str, days: int):
    """Instantiate, validate, and return (connector, source_label) for the given cloud."""

    if cloud == "aws":
        from .connectors.aws import AWSConnector

        with console.status("[bold cyan]Connecting to AWS...[/bold cyan]", spinner="dots"):
            try:
                connector = AWSConnector(profile=profile)
                account_id = connector.validate_credentials()
            except RuntimeError as e:
                console.print(f"\n[red bold]Error:[/red bold] {e}")
                raise typer.Exit(1)
        console.print(f"[green]✓[/green] Connected  [bold]AWS account {account_id}[/bold]")
        console.print(
            f"[dim]  Reading last {days} days of Cost Explorer data (this costs ~$0.03)...[/dim]\n"
        )
        return connector, f"AWS account {account_id} (last {days}d)"

    elif cloud == "gcp":
        from .connectors.gcp import GCPConnector

        with console.status("[bold cyan]Connecting to GCP...[/bold cyan]", spinner="dots"):
            try:
                connector = GCPConnector(project=profile)
                project_id = connector.validate_credentials()
            except RuntimeError as e:
                console.print(f"\n[red bold]Error:[/red bold] {e}")
                raise typer.Exit(1)
        console.print(f"[green]✓[/green] Connected  [bold]GCP project {project_id}[/bold]")
        console.print(
            "[dim]  Reading running resources (estimated cost from catalog prices)...[/dim]\n"
        )
        return connector, f"GCP project {project_id}"

    elif cloud == "azure":
        from .connectors.azure import AzureConnector

        with console.status("[bold cyan]Connecting to Azure...[/bold cyan]", spinner="dots"):
            try:
                connector = AzureConnector(subscription_id=profile)
                sub_id = connector.validate_credentials()
            except RuntimeError as e:
                console.print(f"\n[red bold]Error:[/red bold] {e}")
                raise typer.Exit(1)
        console.print(f"[green]✓[/green] Connected  [bold]Azure subscription {sub_id}[/bold]")
        console.print(
            "[dim]  Reading provisioned VMs (estimated cost from catalog prices)...[/dim]\n"
        )
        return connector, f"Azure subscription {sub_id}"

    else:
        console.print(
            f"[red]Unknown cloud:[/red] {cloud!r}. Supported: [bold]aws[/bold], [bold]gcp[/bold], [bold]azure[/bold]"
        )
        raise typer.Exit(1)


@app.command()
def analyze(
    source: str | None = typer.Argument(
        None, help="Terraform directory, .tf file, or plan JSON (default: current directory)"
    ),
    cloud: str = typer.Option("", "--cloud", "-c", help="Read live resources: aws | gcp | azure"),
    days: int = typer.Option(30, "--days", help="Days of billing history (with --cloud)"),
    profile: str = typer.Option("", "--profile", help="Cloud credential profile name"),
    region: str = typer.Option(
        "",
        "--region",
        "-r",
        help="Cloud region (e.g. us-east-1, eu-west-1, ap-southeast-1). Default: us-east-1",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Launch interactive split-panel TUI"
    ),
    fallback: bool = typer.Option(False, "--fallback", help=_FALLBACK_HELP),
    live: bool = typer.Option(False, "--live", help=_LIVE_HELP),
) -> None:
    """Surface every cost-saving strategy for your infrastructure.

    Works with Terraform files OR live billing data from your cloud account.

    Strategies surfaced: region shift, Graviton/ARM, reserved instances,
    hybrid compute offload, full provider migration, spot instances.

    Examples:
      cloudslayer analyze ./terraform/
      cloudslayer analyze --cloud aws
      cloudslayer analyze --cloud gcp
      cloudslayer analyze --cloud azure --days 7
      cloudslayer analyze --cloud aws --days 7 --profile prod
    """
    _configure_pricing(fallback, live)
    if region:
        from .config import set_region

        set_region(region)

    from .analyzer import load_from_cloud, load_from_terraform_detailed
    from .strategies import run_all_strategies

    scan_report = None
    if cloud:
        cloud = cloud.lower()
        connector, source_label = _make_connector(cloud, profile, days)
        with console.status(
            f"[bold cyan]Reading {cloud.upper()} resources...[/bold cyan]", spinner="dots"
        ):
            resources = load_from_cloud(connector, days)

        if not resources:
            _render_pricing_warnings()
            console.print(
                "[yellow]No resources found. Check credentials and project/subscription.[/yellow]"
            )
            raise typer.Exit(1)

    else:
        path = source or "."
        with console.status("[bold cyan]Scanning Terraform files...[/bold cyan]", spinner="dots"):
            resources, scan_report = load_from_terraform_detailed(path)

        if not resources:
            _render_pricing_warnings()
            if scan_report and scan_report.supported:
                console.print(
                    "[yellow]Resources were recognized, but their current pricing is unavailable.[/yellow]"
                )
            else:
                console.print(f"[yellow]No recognized cloud resources found in {path}[/yellow]")
            raise typer.Exit(1)

        source_label = path

    with console.status("[bold cyan]Computing strategies...[/bold cyan]", spinner="dots"):
        strategies = run_all_strategies(resources)
    if not interactive:
        _render_pricing_warnings()

    if interactive:
        from .pricing import pricing_warnings
        from .tui import AnalyzeTUI

        AnalyzeTUI(
            resources,
            strategies,
            source_label=source_label,
            pricing_warnings=pricing_warnings(),
        ).run()
        return

    if scan_report is not None:
        console.print()
        _render_coverage(scan_report, len(resources))
    render_analyze(resources, strategies, source_label)


@app.command()
def scan(
    directory: Path = typer.Argument(
        Path("."), help="Terraform directory, .tf file, or `terraform show -json` plan file"
    ),
    generate_spec: bool = typer.Option(
        False, "--generate-spec", "-g", help="Output a cloudslayer .hcl spec to stdout"
    ),
) -> None:
    """Scan Terraform files (or a plan JSON) and detect cloud resources for cost comparison.

    For full accuracy on repos using modules, variables, count or for_each,
    scan the resolved plan instead of raw HCL:

      terraform plan -out=plan.out && terraform show -json plan.out > plan.json
      cloudslayer scan plan.json
    """
    from .scanner import generate_spec as make_spec
    from .scanner import scan_path

    report = scan_path(str(directory))
    resources = report.supported

    if not resources and not report.uncosted:
        console.print(f"[yellow]No recognized cloud resources found in {directory}[/yellow]")
        raise typer.Exit(1)

    if generate_spec:
        print(make_spec(resources))
        return

    console.print(
        f"\nFound [bold]{report.total_seen}[/bold] resources in [cyan]{directory}[/cyan]:\n"
    )
    for r in resources:
        console.print(
            f"  [cyan]{r.terraform_type}[/cyan].[bold]{r.resource_name}[/bold]"
            f"  →  {r.cloudslayer_type}  [dim]({r.source_file})[/dim]"
        )
    console.print()
    _render_coverage(report)

    console.print(
        f"  Run [bold]cloudslayer scan {directory} --generate-spec > infra.hcl[/bold] to create a cost spec,"
        f"\n  then [bold]cloudslayer plan infra.hcl[/bold] to compare costs.\n"
    )


@app.command()
def init(
    output: Path = typer.Option(
        Path(".github/workflows/cloudslayer.yml"),
        "--output",
        "-o",
        help="Where to write the workflow file",
    ),
    spec_file: str = typer.Option(
        "infra.hcl", "--spec", "-s", help="Path to your cloudslayer spec file"
    ),
) -> None:
    """Generate a GitHub Actions workflow for automated cost checking on PRs."""
    workflow = _github_actions_template(spec_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(workflow)
    console.print(f"[green]✓[/green] Written to [bold]{output}[/bold]")
    console.print(
        "  Commit this file and cloudslayer will comment cost comparisons on every PR "
        "that touches your infrastructure spec.\n"
        "  On PRs, [bold]cloudslayer diff[/bold] automatically shows the cost delta vs the base branch."
    )


def _github_actions_template(spec_file: str) -> str:
    return f"""\
name: Cloud Cost Check

on:
  pull_request:
    paths:
      - '{spec_file}'
      - '**/*.hcl'
      - '**/main.tf'

jobs:
  cloudslayer:
    name: Cost Comparison
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Install cloudslayer
        run: uv pip install --system cloudslayer

      - name: Save base spec
        run: git show origin/${{{{ github.base_ref }}}}:{spec_file} > /tmp/infra-before.hcl 2>/dev/null || cp {spec_file} /tmp/infra-before.hcl

      - name: Run cost diff
        run: |
          cloudslayer diff /tmp/infra-before.hcl {spec_file} | tee cloudslayer-report.txt || true
          cloudslayer plan {spec_file} --format json > cloudslayer-costs.json

      - name: Comment on PR
        uses: actions/github-script@v7
        if: github.event_name == 'pull_request'
        with:
          script: |
            const fs = require('fs');
            const report = fs.readFileSync('cloudslayer-report.txt', 'utf8');
            const body = [
              '## ☁️ Cloud Cost Impact',
              '',
              '<details><summary>View full cost diff</summary>',
              '',
              '```',
              report.trim().slice(0, 4000),
              '```',
              '</details>',
              '',
              `> Generated by [cloudslayer](https://github.com/cloudslayer-dev/cloudslayer)`,
            ].join('\\n');
            const comments = await github.rest.issues.listComments({{
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
            }});
            const existing = comments.data.find(c => c.body.includes('☁️ Cloud Cost Impact'));
            if (existing) {{
              await github.rest.issues.updateComment({{
                comment_id: existing.id,
                owner: context.repo.owner,
                repo: context.repo.repo,
                body,
              }});
            }} else {{
              await github.rest.issues.createComment({{
                issue_number: context.issue.number,
                owner: context.repo.owner,
                repo: context.repo.repo,
                body,
              }});
            }}

      - name: Upload report
        uses: actions/upload-artifact@v4
        with:
          name: cloudslayer-report
          path: |
            cloudslayer-report.txt
            cloudslayer-costs.json
"""


@app.command()
def providers(
    fallback: bool = typer.Option(False, "--fallback", help=_FALLBACK_HELP),
    live: bool = typer.Option(False, "--live", help=_LIVE_HELP),
) -> None:
    """List all supported cloud providers, resource types, and pricing sources."""
    _configure_pricing(fallback, live)
    from .pricing import PricingUnavailableError
    from .providers import ALL_OBJECT_STORAGE_PROVIDERS
    from .providers.compute import ALL_COMPUTE_PROVIDERS
    from .providers.database import ALL_DATABASE_PROVIDERS

    console.print()
    console.print(f"[bold]Object Storage[/bold]  ({len(ALL_OBJECT_STORAGE_PROVIDERS)} providers)\n")
    for p in ALL_OBJECT_STORAGE_PROVIDERS:
        try:
            pricing = p.get_pricing()
        except PricingUnavailableError as error:
            console.print(
                f"  [cyan]{p.display_name:<28}[/cyan]  [yellow]unavailable[/yellow]  [dim]{error.detail}[/dim]"
            )
            continue
        source = pricing.price_source or pricing.last_verified or "unknown"
        console.print(
            f"  [cyan]{p.display_name:<28}[/cyan]"
            f"  ${pricing.storage_per_gb_mo:.4f}/GB/mo"
            f"  egress ${pricing.egress_per_gb:.4f}/GB"
            f"  [dim]{source}[/dim]"
        )

    console.print(f"\n[bold]Compute[/bold]  ({len(ALL_COMPUTE_PROVIDERS)} providers)\n")
    for p in ALL_COMPUTE_PROVIDERS:
        try:
            instances = p.catalog()
        except PricingUnavailableError as error:
            console.print(
                f"  [cyan]{p.display_name:<28}[/cyan]  [yellow]unavailable[/yellow]  [dim]{error.detail}[/dim]"
            )
            continue
        price_range = f"${min(i.price_per_month for i in instances):.2f}–${max(i.price_per_month for i in instances):.2f}/mo"
        sources = ", ".join(sorted({item.price_source or "unknown" for item in instances}))
        console.print(
            f"  [cyan]{p.display_name:<28}[/cyan]  {len(instances):>2} instance types  "
            f"{price_range}  [dim]{sources}[/dim]"
        )

    console.print(f"\n[bold]Database[/bold]  ({len(ALL_DATABASE_PROVIDERS)} providers)\n")
    for p in ALL_DATABASE_PROVIDERS:
        try:
            db_plans = p.plans()
        except PricingUnavailableError as error:
            console.print(
                f"  [cyan]{p.display_name:<28}[/cyan]  [yellow]unavailable[/yellow]  [dim]{error.detail}[/dim]"
            )
            continue
        price_range = f"${min(pl.base_price for pl in db_plans):.2f}–${max(pl.base_price for pl in db_plans):.2f}/mo"
        sources = ", ".join(sorted({plan.price_source or "unknown" for plan in db_plans}))
        console.print(
            f"  [cyan]{p.display_name:<28}[/cyan]  {len(db_plans):>2} plans  "
            f"{price_range}  [dim]{sources}[/dim]"
        )
    console.print()


@app.command()
def cache(
    action: str = typer.Argument("status", help="Action: [bold]status[/bold] | [bold]clear[/bold]"),
) -> None:
    """Manage the local pricing cache (~/.cloudslayer/cache/).

    cloudslayer caches live pricing data for 7 days to avoid repeated API calls.
    Use [bold]clear[/bold] to force a refresh on next run.
    """
    import time

    cache_dir = Path.home() / ".cloudslayer" / "cache"

    if action == "clear":
        if not cache_dir.exists():
            console.print("[dim]Cache directory does not exist — nothing to clear.[/dim]")
            return
        count = sum(1 for f in cache_dir.glob("*.json") if f.unlink() is None)  # type: ignore[func-returns-value]
        console.print(
            f"[green]✓[/green] Cleared {count} cached pricing file(s). Fresh data will be fetched on next run."
        )

    elif action == "status":
        files = sorted(cache_dir.glob("*.json")) if cache_dir.exists() else []
        if not files:
            console.print(
                "[dim]No cached pricing data. Run[/dim] [bold]cloudslayer plan[/bold] [dim]to populate.[/dim]"
            )
            return
        console.print(f"\n[bold]Pricing cache[/bold]  [dim]({cache_dir})[/dim]\n")
        for f in files:
            age_secs = time.time() - f.stat().st_mtime
            age_hrs = age_secs / 3600
            age_str = f"{age_hrs:.0f}h old" if age_hrs < 48 else f"{age_hrs / 24:.0f}d old"
            size_kb = f.stat().st_size / 1024
            stale = "  [yellow]stale[/yellow]" if age_hrs > 24 * 7 else ""
            console.print(f"  [cyan]{f.name:<44}[/cyan]  {size_kb:>6.0f} KB  {age_str}{stale}")
        console.print()

    else:
        console.print(
            f"[red]Unknown action:[/red] {action!r}. Use [bold]status[/bold] or [bold]clear[/bold]."
        )
        raise typer.Exit(1)


@app.command()
def actual(
    cloud: str = typer.Argument(
        "aws", help="Cloud provider: [bold]aws[/bold] | [bold]gcp[/bold] | [bold]azure[/bold]"
    ),
    days: int = typer.Option(
        30,
        "--days",
        "-d",
        help="Days of billing history (AWS only; GCP/Azure read current resources)",
    ),
    profile: str = typer.Option(
        "", "--profile", help="AWS profile / GCP project ID / Azure subscription ID"
    ),
    top: int = typer.Option(
        0, "--top", "-n", help="Show only N cheapest alternatives per resource"
    ),
    provider: str = typer.Option(
        "", "--provider", "-p", help="Filter comparison providers (comma-separated)"
    ),
    fallback: bool = typer.Option(False, "--fallback", help=_FALLBACK_HELP),
    live: bool = typer.Option(False, "--live", help=_LIVE_HELP),
) -> None:
    """Show your actual cloud spend vs what you'd pay on every alternative.

    AWS reads real Cost Explorer billing data. GCP and Azure read your currently
    running resources and estimate costs from catalog prices.

    [bold]AWS requirements:[/bold]
      • AWS credentials configured ([dim]aws configure[/dim] or env vars)
      • IAM permission: [dim]ce:GetCostAndUsage[/dim]
      • Cost Explorer enabled in your AWS account
      • Note: AWS charges $0.01 per Cost Explorer API request

    [bold]GCP requirements:[/bold]
      • [dim]gcloud auth application-default login[/dim]
      • Enabled APIs: compute.googleapis.com, sqladmin.googleapis.com

    [bold]Azure requirements:[/bold]
      • [dim]az login[/dim] or set [dim]AZURE_CLIENT_ID/SECRET/TENANT_ID[/dim]
      • Set [dim]AZURE_SUBSCRIPTION_ID[/dim] or pass via [dim]--profile[/dim]

    [bold]Examples:[/bold]
      cloudslayer actual aws
      cloudslayer actual gcp
      cloudslayer actual azure
      cloudslayer actual aws --days 7 --profile prod --top 4
      cloudslayer actual gcp --profile my-project-id
    """
    from rich.rule import Rule

    _configure_pricing(fallback, live)
    cloud = cloud.lower()
    connector, source_label = _make_connector(cloud, profile, days)

    with console.status("[bold cyan]Fetching resource data...[/bold cyan]", spinner="dots"):
        try:
            resources = connector.get_spend(days=days)
        except Exception as e:
            console.print(f"[red bold]Failed to fetch data:[/red bold] {e}")
            if cloud == "aws":
                console.print(
                    "[dim]Make sure Cost Explorer is enabled: https://console.aws.amazon.com/cost-management/home[/dim]"
                )
            raise typer.Exit(1)

    if not resources:
        console.print("[yellow]No resources found.[/yellow]")
        if cloud == "aws":
            console.print("[dim]• Check that Cost Explorer is enabled in your AWS account[/dim]")
            console.print("[dim]• Try a longer window:  cloudslayer actual aws --days 90[/dim]")
        raise typer.Exit(0)

    total_actual = sum(r.actual_monthly_cost for r in resources)
    services = sorted({r.service.upper() for r in resources})
    console.print(
        f"Found [bold]{len(resources)}[/bold] resource type(s) across "
        f"[bold]{', '.join(services)}[/bold]  —  "
        f"[yellow bold]${total_actual:,.2f}/mo[/yellow bold] estimated spend\n"
    )

    console.print()
    console.print(
        Rule(
            f"[bold cyan]cloudslayer actual[/bold cyan]  {source_label}  vs alternatives",
            style="cyan",
        )
    )
    console.print()

    current_spend = 0.0
    cheapest_spend = 0.0

    for resource in resources:
        current_spend += resource.actual_monthly_cost
        svc = resource.service.upper()
        console.print(
            f"  [dim]{svc} — {resource.display_name}: "
            f"[yellow bold]${resource.actual_monthly_cost:,.2f}/mo[/yellow bold][/dim]"
        )

        if resource.compute_spec:
            results = _filter_results(plan_compute(resource.compute_spec), provider)
            if results:
                cheapest_spend += min(r.total for r in results)
                render_compute_comparison(
                    resource.compute_spec,
                    results,
                    top=top,
                    current_provider=resource.current_provider,
                    instance_label=resource.display_name,
                )

        elif resource.storage_spec:
            results_s = _filter_results(plan_object_storage(resource.storage_spec), provider)
            if results_s:
                cheapest_spend += min(r.total for r in results_s)
                render_storage_comparison(
                    resource.storage_spec,
                    results_s,
                    top=top,
                    current_provider=resource.current_provider,
                )

        elif resource.database_spec:
            results_d = _filter_results(plan_database(resource.database_spec), provider)
            if results_d:
                cheapest_spend += min(r.total for r in results_d)
                render_database_comparison(
                    resource.database_spec,
                    results_d,
                    top=top,
                    current_provider=resource.current_provider,
                    instance_label=resource.display_name,
                )

    _render_pricing_warnings()

    total_savings = current_spend - cheapest_spend
    console.print(Rule("[bold]Total Savings Opportunity[/bold]", style="cyan"))
    console.print()
    console.print(
        f"  Current spend:             [yellow bold]${current_spend:,.2f}/mo[/yellow bold]  (${current_spend * 12:,.0f}/yr)"
    )
    console.print(
        f"  Cheapest combination:      [green bold]${cheapest_spend:,.2f}/mo[/green bold]  (${cheapest_spend * 12:,.0f}/yr)"
    )
    if total_savings > 0.01:
        console.print(
            f"\n  [bold]You could save[/bold] [green bold]${total_savings:,.2f}/mo[/green bold]"
            f" — that's [green bold]${total_savings * 12:,.0f}/yr[/green bold] by switching providers.\n"
        )
    else:
        console.print("\n  [green]You're already on the cheapest available options.[/green]\n")


@app.command()
def version() -> None:
    """Show the cloudslayer version."""
    try:
        import importlib.metadata

        ver = importlib.metadata.version("cloudslayer")
    except Exception:
        ver = "unknown"
    console.print(f"cloudslayer [bold]{ver}[/bold]")


if __name__ == "__main__":
    app()
