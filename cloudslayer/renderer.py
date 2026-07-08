from __future__ import annotations

from rich import box
from rich.rule import Rule
from rich.table import Table

from .console import console
from .models import (
    ComputeResult,
    ComputeSpec,
    CostResult,
    DatabaseResult,
    DatabaseSpec,
    ObjectStorageSpec,
    ServerlessResult,
    ServerlessSpec,
)


def _source_badge(source: str) -> str:
    return f" [dim]({source})[/dim]" if source else ""


def render_header() -> None:
    console.print()
    console.print(
        Rule("[bold cyan]cloudslayer[/bold cyan]  multi-cloud cost comparison", style="cyan")
    )
    console.print()


def _vs_cell(result_total: float, cheapest_total: float) -> str:
    if result_total == cheapest_total:
        return "[green]cheapest[/green]"
    diff = result_total - cheapest_total
    pct = (diff / cheapest_total * 100) if cheapest_total > 0 else 0
    return f"[red]+${diff:,.2f} (+{pct:.0f}%)[/red]"


def _vs_current_cell(result_total: float, current_total: float, is_current: bool) -> str:
    if is_current:
        return "[bold yellow]← you are here[/bold yellow]"
    diff = current_total - result_total
    pct = (diff / current_total * 100) if current_total > 0 else 0
    if diff > 0.01:
        return f"[green]save ${diff:,.2f}/mo ({pct:.0f}%)[/green]"
    diff2 = result_total - current_total
    pct2 = (diff2 / current_total * 100) if current_total > 0 else 0
    return f"[red]+${diff2:,.2f} more ({pct2:.0f}%)[/red]"


def _delta_cell(before: float, after: float) -> str:
    diff = after - before
    if abs(diff) < 0.01:
        return "[dim]no change[/dim]"
    pct = (diff / before * 100) if before > 0 else 0
    sign = "+" if diff > 0 else ""
    colour = "red" if diff > 0 else "green"
    return f"[{colour}]{sign}${diff:,.2f} ({sign}{pct:.0f}%)[/{colour}]"


def render_storage_comparison(
    spec: ObjectStorageSpec,
    results: list[CostResult],
    top: int = 0,
    current_provider: str = "",
) -> None:
    sorted_results = sorted(results, key=lambda r: r.total)
    if top > 0:
        sorted_results = sorted_results[:top]
    cheapest = sorted_results[0]
    most_expensive = sorted_results[-1]
    current_result = next((r for r in sorted_results if r.provider == current_provider), None)
    use_current = current_result is not None

    usage = (
        f"{spec.storage_gb:,.0f} GB stored  ·  {spec.get_requests:,} GET/mo  ·  "
        f"{spec.put_requests:,} PUT/mo  ·  {spec.egress_gb:,.0f} GB egress/mo"
    )
    vs_label = "vs Current" if use_current else "vs Cheapest"

    table = Table(
        title=f'[bold]object_storage[/bold] [cyan]"{spec.name}"[/cyan]\n[dim]{usage}[/dim]',
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Provider", min_width=24, no_wrap=True)
    table.add_column("Storage", justify="right", min_width=9)
    table.add_column("Requests", justify="right", min_width=9)
    table.add_column("Egress", justify="right", min_width=9)
    table.add_column("Monthly", justify="right", min_width=10, style="bold")
    table.add_column(vs_label, justify="right", min_width=24, no_wrap=True)

    for result in sorted_results:
        is_current = result.provider == current_provider
        is_cheapest = result is cheapest
        label = (
            f"[yellow]→ {result.display_name} (current)[/yellow]"
            if is_current
            else f"[green]★ {result.display_name}[/green]"
            if is_cheapest
            else result.display_name
        )
        label += _source_badge(result.price_source)
        vs = (
            _vs_current_cell(result.total, current_result.total, is_current)
            if use_current
            else _vs_cell(result.total, cheapest.total)
        )
        table.add_row(
            label,
            f"${result.storage_cost:,.2f}",
            f"${result.request_cost:,.2f}",
            f"${result.egress_cost:,.2f}",
            f"${result.total:,.2f}",
            vs,
        )

    console.print(table)
    if cheapest.notes:
        console.print(f"  [dim]★  {cheapest.notes}[/dim]")
    if use_current and current_result is not cheapest:
        savings = current_result.total - cheapest.total
        console.print(
            f"\n  [bold]Switch recommendation:[/bold] [green]{cheapest.display_name}[/green] saves "
            f"[bold green]${savings:,.2f}/mo[/bold green] (${savings * 12:,.0f}/yr) vs your current "
            f"[yellow]{current_result.display_name}[/yellow] at ${current_result.total:,.2f}/mo"
        )
    elif len(sorted_results) > 1 and not use_current:
        savings = most_expensive.total - cheapest.total
        console.print(
            f"\n  [bold]Recommendation:[/bold] [green]{cheapest.display_name}[/green] saves "
            f"[bold]${savings:,.2f}/mo[/bold] (${savings * 12:,.2f}/yr) vs {most_expensive.display_name}"
        )
    console.print()


def render_compute_comparison(
    spec: ComputeSpec,
    results: list[ComputeResult],
    top: int = 0,
    current_provider: str = "",
    instance_label: str = "",
) -> None:
    sorted_results = sorted(results, key=lambda r: r.total)
    if top > 0:
        sorted_results = sorted_results[:top]
    cheapest = sorted_results[0]
    most_expensive = sorted_results[-1]
    current_result = next((r for r in sorted_results if r.provider == current_provider), None)
    use_current = current_result is not None
    vs_label = "vs Current" if use_current else "vs Cheapest"

    subtitle = f"{spec.vcpu} vCPU · {spec.memory_gb:.0f} GB RAM"
    if instance_label:
        subtitle += f"  [dim](from {instance_label})[/dim]"
    elif not use_current:
        subtitle += " (minimum)"

    table = Table(
        title=f'[bold]compute[/bold] [cyan]"{spec.name}"[/cyan]\n[dim]{subtitle}[/dim]',
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Provider", min_width=24, no_wrap=True)
    table.add_column("Instance", min_width=16, no_wrap=True)
    table.add_column("vCPU", justify="right", min_width=5)
    table.add_column("RAM", justify="right", min_width=7)
    table.add_column("Monthly", justify="right", min_width=10, style="bold")
    table.add_column(vs_label, justify="right", min_width=24, no_wrap=True)

    for result in sorted_results:
        is_current = result.provider == current_provider
        is_cheapest = result is cheapest
        label = (
            f"[yellow]→ {result.display_name} (current)[/yellow]"
            if is_current
            else f"[green]★ {result.display_name}[/green]"
            if is_cheapest
            else result.display_name
        )
        label += _source_badge(result.price_source)
        vs = (
            _vs_current_cell(result.total, current_result.total, is_current)
            if use_current
            else _vs_cell(result.total, cheapest.total)
        )
        table.add_row(
            label,
            result.instance_name,
            str(result.instance_vcpu),
            f"{result.instance_memory_gb:.0f} GB",
            f"${result.total:,.2f}",
            vs,
        )

    console.print(table)
    if cheapest.notes:
        console.print(f"  [dim]★  {cheapest.notes}[/dim]")
    if use_current and current_result is not cheapest:
        savings = current_result.total - cheapest.total
        console.print(
            f"\n  [bold]Switch recommendation:[/bold] [green]{cheapest.display_name}[/green] ({cheapest.instance_name}) saves "
            f"[bold green]${savings:,.2f}/mo[/bold green] (${savings * 12:,.0f}/yr) vs your current "
            f"[yellow]{current_result.display_name}[/yellow] ${current_result.total:,.2f}/mo"
        )
    elif len(sorted_results) > 1 and not use_current:
        savings = most_expensive.total - cheapest.total
        console.print(
            f"\n  [bold]Recommendation:[/bold] [green]{cheapest.display_name}[/green] ({cheapest.instance_name}) saves "
            f"[bold]${savings:,.2f}/mo[/bold] (${savings * 12:,.2f}/yr) vs {most_expensive.display_name}"
        )
    console.print()


def render_database_comparison(
    spec: DatabaseSpec,
    results: list[DatabaseResult],
    top: int = 0,
    current_provider: str = "",
    instance_label: str = "",
) -> None:
    sorted_results = sorted(results, key=lambda r: r.total)
    if top > 0:
        sorted_results = sorted_results[:top]
    cheapest = sorted_results[0]
    most_expensive = sorted_results[-1]
    current_result = next((r for r in sorted_results if r.provider == current_provider), None)
    use_current = current_result is not None
    vs_label = "vs Current" if use_current else "vs Cheapest"

    subtitle = f"{spec.vcpu} vCPU · {spec.memory_gb:.0f} GB RAM · {spec.storage_gb:.0f} GB storage · {spec.engine}"
    if instance_label:
        subtitle += f"  [dim](from {instance_label})[/dim]"

    table = Table(
        title=f'[bold]database[/bold] [cyan]"{spec.name}"[/cyan]\n[dim]{subtitle}[/dim]',
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Provider", min_width=18, no_wrap=True)
    table.add_column("Plan", min_width=20, no_wrap=True)
    table.add_column("vCPU", justify="right", min_width=5)
    table.add_column("RAM", justify="right", min_width=8)
    table.add_column("Instance", justify="right", min_width=9)
    table.add_column("Storage", justify="right", min_width=9)
    table.add_column("Monthly", justify="right", min_width=10, style="bold")
    table.add_column(vs_label, justify="right", min_width=24, no_wrap=True)

    for result in sorted_results:
        is_current = result.provider == current_provider
        is_cheapest = result is cheapest
        label = (
            f"[yellow]→ {result.display_name} (current)[/yellow]"
            if is_current
            else f"[green]★ {result.display_name}[/green]"
            if is_cheapest
            else result.display_name
        )
        label += _source_badge(result.price_source)
        vs = (
            _vs_current_cell(result.total, current_result.total, is_current)
            if use_current
            else _vs_cell(result.total, cheapest.total)
        )
        table.add_row(
            label,
            result.plan_name,
            str(result.plan_vcpu),
            f"{result.plan_memory_gb:.1f} GB",
            f"${result.instance_cost:,.2f}",
            f"${result.storage_cost:,.2f}",
            f"${result.total:,.2f}",
            vs,
        )

    console.print(table)
    if cheapest.notes:
        console.print(f"  [dim]★  {cheapest.notes}[/dim]")
    if use_current and current_result is not cheapest:
        savings = current_result.total - cheapest.total
        console.print(
            f"\n  [bold]Switch recommendation:[/bold] [green]{cheapest.display_name}[/green] ({cheapest.plan_name}) saves "
            f"[bold green]${savings:,.2f}/mo[/bold green] (${savings * 12:,.0f}/yr) vs your current "
            f"[yellow]{current_result.display_name}[/yellow] ${current_result.total:,.2f}/mo"
        )
    elif len(sorted_results) > 1 and not use_current:
        savings = most_expensive.total - cheapest.total
        console.print(
            f"\n  [bold]Recommendation:[/bold] [green]{cheapest.display_name}[/green] ({cheapest.plan_name}) saves "
            f"[bold]${savings:,.2f}/mo[/bold] (${savings * 12:,.2f}/yr) vs {most_expensive.display_name}"
        )
    console.print()


def render_serverless_comparison(
    spec: ServerlessSpec,
    results: list[ServerlessResult],
    top: int = 0,
    current_provider: str = "",
) -> None:
    sorted_results = sorted(results, key=lambda r: r.monthly_cost)
    if top > 0:
        sorted_results = sorted_results[:top]
    cheapest = sorted_results[0]
    most_expensive = sorted_results[-1]
    current_result = next((r for r in sorted_results if r.provider == current_provider), None)
    use_current = current_result is not None
    vs_label = "vs Current" if use_current else "vs Cheapest"

    subtitle = (
        f"{spec.invocations_per_month:,} invocations/mo  ·  "
        f"{spec.avg_duration_ms:.0f}ms avg  ·  {spec.memory_mb} MB"
    )

    table = Table(
        title=f'[bold]serverless[/bold] [cyan]"{spec.name}"[/cyan]\n[dim]{subtitle}[/dim]',
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Provider", min_width=24, no_wrap=True)
    table.add_column("Monthly", justify="right", min_width=10, style="bold")
    table.add_column("Per 1M Req", justify="right", min_width=12)
    table.add_column("Notes", min_width=28)
    table.add_column(vs_label, justify="right", min_width=24, no_wrap=True)

    for result in sorted_results:
        is_current = result.provider == current_provider
        is_cheapest = result is cheapest
        label = (
            f"[yellow]→ {result.display_name} (current)[/yellow]"
            if is_current
            else f"[green]★ {result.display_name}[/green]"
            if is_cheapest
            else result.display_name
        )
        label += _source_badge(result.price_source)
        vs = (
            _vs_current_cell(result.monthly_cost, current_result.monthly_cost, is_current)
            if use_current
            else _vs_cell(result.monthly_cost, cheapest.monthly_cost)
        )
        table.add_row(
            label,
            f"${result.monthly_cost:,.4f}",
            f"${result.per_million_requests:,.4f}",
            result.notes,
            vs,
        )

    console.print(table)
    if cheapest.notes:
        console.print(f"  [dim]★  {cheapest.notes}[/dim]")
    if use_current and current_result is not cheapest:
        savings = current_result.monthly_cost - cheapest.monthly_cost
        console.print(
            f"\n  [bold]Switch recommendation:[/bold] [green]{cheapest.display_name}[/green] saves "
            f"[bold green]${savings:,.4f}/mo[/bold green] (${savings * 12:,.2f}/yr) vs your current "
            f"[yellow]{current_result.display_name}[/yellow] at ${current_result.monthly_cost:,.4f}/mo"
        )
    elif len(sorted_results) > 1 and not use_current:
        savings = most_expensive.monthly_cost - cheapest.monthly_cost
        console.print(
            f"\n  [bold]Recommendation:[/bold] [green]{cheapest.display_name}[/green] saves "
            f"[bold]${savings:,.4f}/mo[/bold] (${savings * 12:,.2f}/yr) vs {most_expensive.display_name}"
        )
    console.print()


def render_total_summary(
    storage_results: list[tuple],
    compute_results: list[tuple],
    database_results: list[tuple],
    serverless_results: list[tuple] | None = None,
) -> None:
    serverless_results = serverless_results or []
    cheapest_total = (
        sum(min(r.total for r in rs) for _, rs in storage_results if rs)
        + sum(min(r.total for r in rs) for _, rs in compute_results if rs)
        + sum(min(r.total for r in rs) for _, rs in database_results if rs)
        + sum(min(r.monthly_cost for r in rs) for _, rs in serverless_results if rs)
    )
    expensive_total = (
        sum(max(r.total for r in rs) for _, rs in storage_results if rs)
        + sum(max(r.total for r in rs) for _, rs in compute_results if rs)
        + sum(max(r.total for r in rs) for _, rs in database_results if rs)
        + sum(max(r.monthly_cost for r in rs) for _, rs in serverless_results if rs)
    )
    max_savings = expensive_total - cheapest_total

    console.print(Rule("[bold]Total Infrastructure Summary[/bold]", style="cyan"))
    console.print()

    # Recommended stack breakdown
    stack_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    stack_table.add_column("Resource", style="dim")
    stack_table.add_column("Provider", style="green")
    stack_table.add_column("Plan", style="dim")
    stack_table.add_column("Monthly", justify="right", style="bold")

    for spec, results in storage_results:
        if results:
            best = min(results, key=lambda r: r.total)
            stack_table.add_row(
                f'storage "{spec.name}"', best.display_name, "", f"${best.total:,.2f}/mo"
            )
    for spec, results in compute_results:
        if results:
            best = min(results, key=lambda r: r.total)
            stack_table.add_row(
                f'compute "{spec.name}"',
                best.display_name,
                best.instance_name,
                f"${best.total:,.2f}/mo",
            )
    for spec, results in database_results:
        if results:
            best = min(results, key=lambda r: r.total)
            stack_table.add_row(
                f'database "{spec.name}"',
                best.display_name,
                best.plan_name,
                f"${best.total:,.2f}/mo",
            )
    for spec, results in serverless_results:
        if results:
            best = min(results, key=lambda r: r.monthly_cost)
            stack_table.add_row(
                f'serverless "{spec.name}"', best.display_name, "", f"${best.monthly_cost:,.4f}/mo"
            )

    console.print("  [bold]Recommended stack[/bold] (cheapest per resource):\n")
    console.print(stack_table)
    console.print(
        f"  Cheapest combination:     [bold green]${cheapest_total:,.2f}/mo[/bold green]  (${cheapest_total * 12:,.0f}/yr)"
    )
    console.print(
        f"  Most expensive combo:     [bold red]${expensive_total:,.2f}/mo[/bold red]  (${expensive_total * 12:,.0f}/yr)"
    )
    console.print(
        f"  Maximum possible savings: [bold]${max_savings:,.2f}/mo[/bold]  (${max_savings * 12:,.0f}/yr)\n"
    )


def render_diff(
    before_storage: list[tuple],
    before_compute: list[tuple],
    before_database: list[tuple],
    after_storage: list[tuple],
    after_compute: list[tuple],
    after_database: list[tuple],
) -> None:
    console.print()
    console.print(
        Rule("[bold cyan]cloudslayer diff[/bold cyan]  cost impact of your changes", style="cyan")
    )
    console.print()

    before_cheapest_total = 0.0
    after_cheapest_total = 0.0

    def _find_by_name(pairs: list[tuple], name: str):
        return next((rs for spec, rs in pairs if spec.name == name), None)

    for resource_type, before_pairs, after_pairs in [
        ("object_storage", before_storage, after_storage),
        ("compute", before_compute, after_compute),
        ("database", before_database, after_database),
    ]:
        before_names = {spec.name for spec, _ in before_pairs}
        after_names = {spec.name for spec, _ in after_pairs}

        for name in sorted(before_names | after_names):
            before_rs = _find_by_name(before_pairs, name)
            after_rs = _find_by_name(after_pairs, name)
            _render_resource_diff(resource_type, name, before_rs, after_rs)
            if before_rs:
                before_cheapest_total += min(r.total for r in before_rs)
            if after_rs:
                after_cheapest_total += min(r.total for r in after_rs)

    console.print(Rule("[bold]Total Impact[/bold]", style="cyan"))
    console.print()
    console.print(
        f"  Before: [bold]${before_cheapest_total:,.2f}/mo[/bold]  (${before_cheapest_total * 12:,.0f}/yr)"
    )
    console.print(
        f"  After:  [bold]${after_cheapest_total:,.2f}/mo[/bold]  (${after_cheapest_total * 12:,.0f}/yr)"
    )
    delta = after_cheapest_total - before_cheapest_total
    sign = "+" if delta >= 0 else ""
    pct = (delta / before_cheapest_total * 100) if before_cheapest_total > 0 else 0
    colour = "red" if delta > 0 else "green"
    console.print(
        f"  Change: [{colour}][bold]{sign}${delta:,.2f}/mo[/bold]  ({sign}{pct:.1f}%)  ({sign}${delta * 12:,.0f}/yr)[/{colour}]"
    )
    console.print()


_PRIORITY_HEADERS = {
    1: "Immediate wins  (no commitment, quick to implement)",
    2: "Commitment purchases  (lock-in required, zero migration effort)",
    3: "Major migrations  (high effort, biggest savings potential)",
}


def render_analyze(resources: list, strategies: list, source_label: str = "") -> None:
    """Render the full strategy analysis report."""
    current_total = sum(r.monthly_cost for r in resources)
    n = len(resources)

    console.print()
    suffix = f"  [dim]{source_label}[/dim]" if source_label else ""
    console.print(
        Rule(
            f"[bold cyan]cloudslayer analyze[/bold cyan]{suffix}"
            f"  ·  {n} resource{'s' if n != 1 else ''}"
            f"  ·  [yellow]${current_total:,.2f}/mo current[/yellow]",
            style="cyan",
        )
    )
    console.print()

    if not strategies:
        console.print(
            "  [green]No cost-saving strategies found — you may already be on optimal pricing.[/green]\n"
        )
        return

    effort_color = {"None": "green", "Low": "green", "Medium": "yellow", "High": "red"}
    risk_color = {"Low": "green", "Medium": "yellow", "High": "red"}

    current_tier: int | None = None
    for i, s in enumerate(strategies, 1):
        if s.priority != current_tier:
            current_tier = s.priority
            header = _PRIORITY_HEADERS.get(s.priority, f"Priority {s.priority}")
            console.print(Rule(f"[bold dim]{header}[/bold dim]", style="dim"))
            console.print()
        ec = effort_color.get(s.effort, "white")
        rc = risk_color.get(s.risk, "white")

        dominant_badge = "  [bold green]★ non-dominated model[/bold green]" if s.is_dominant else ""
        console.print(
            f"  [bold]Strategy {i}[/bold] · [bold cyan]{s.name}[/bold cyan]{dominant_badge}"
        )
        overhead_note = (
            f"  [dim]overhead +${s.overhead_mo:.2f}/mo[/dim]" if s.overhead_mo > 0 else ""
        )
        be_note = ""
        if s.migration_cost_est > 0 and s.break_even_months > 0:
            be_note = f"  [dim]break-even {s.break_even_months:.0f} mo (${s.migration_cost_est:,.0f} migration)[/dim]"
        console.print(
            f"  [green bold]Save ${s.savings_mo:,.2f}/mo ({s.savings_pct:.0f}%)[/green bold]"
            f"    Effort [{ec}]{s.effort}[/{ec}]"
            f"    Risk [{rc}]{s.risk}[/{rc}]" + overhead_note + be_note
        )
        console.print(f"  [dim]{s.pitch}[/dim]")
        console.print()

        if s.items:
            table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1), show_edge=False)
            table.add_column("Resource", style="dim", min_width=16)
            table.add_column("Current")
            table.add_column("Alternative")
            table.add_column("Note", style="dim")
            table.add_column("Save/mo", justify="right")
            for item in s.items:
                save = item.from_cost - item.to_cost
                save_str = f"[green]${save:,.2f}[/green]" if save > 0.005 else "[dim]—[/dim]"
                table.add_row(
                    item.resource_name,
                    item.from_label,
                    item.to_label,
                    item.note,
                    save_str,
                )
            console.print(table)
            console.print()

        for caveat in s.caveats:
            console.print(f"  [yellow]⚠[/yellow]  [dim]{caveat}[/dim]")
        console.print()

    console.print(Rule("[bold]Summary[/bold]", style="cyan"))
    console.print()
    console.print(
        f"  Current spend:    [yellow bold]${current_total:,.2f}/mo[/yellow bold]"
        f"  (${current_total * 12:,.0f}/yr)"
    )
    best = strategies[0]
    saved = best.savings_mo - best.overhead_mo
    console.print(
        f"  Best strategy:    [green bold]save ${saved:,.2f}/mo[/green bold]  ({best.name})"
    )
    console.print(
        f"  → Net spend:      [green bold]${current_total - saved:,.2f}/mo[/green bold]"
        f"  (${(current_total - saved) * 12:,.0f}/yr)"
    )
    console.print()


def _render_resource_diff(resource_type: str, name: str, before_rs, after_rs) -> None:
    if before_rs is None:
        console.print(f'  [green bold]ADDED[/green bold]  {resource_type} [cyan]"{name}"[/cyan]')
        if after_rs:
            cheapest = min(after_rs, key=lambda r: r.total)
            console.print(
                f"  → Cheapest: [green]{cheapest.display_name}[/green]  [bold]${cheapest.total:,.2f}/mo[/bold]\n"
            )
        return

    if after_rs is None:
        console.print(f'  [red bold]REMOVED[/red bold]  {resource_type} [cyan]"{name}"[/cyan]')
        if before_rs:
            cheapest = min(before_rs, key=lambda r: r.total)
            console.print(
                f"  → Was: [dim]{cheapest.display_name}  ${cheapest.total:,.2f}/mo[/dim]\n"
            )
        return

    before_by_provider = {r.display_name: r for r in before_rs}
    after_by_provider = {r.display_name: r for r in after_rs}
    all_providers = sorted(set(before_by_provider) | set(after_by_provider))
    after_cheapest = min(after_rs, key=lambda r: r.total)

    table = Table(
        title=f'[bold]{resource_type}[/bold] [cyan]"{name}"[/cyan]',
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Provider", min_width=22, no_wrap=True)
    table.add_column("Before", justify="right", min_width=11)
    table.add_column("After", justify="right", min_width=11)
    table.add_column("Change", justify="right", min_width=22, no_wrap=True)

    for provider_name in all_providers:
        b = before_by_provider.get(provider_name)
        a = after_by_provider.get(provider_name)
        is_cheapest = provider_name == after_cheapest.display_name
        label = f"[green]★ {provider_name}[/green]" if is_cheapest else provider_name

        if b is None:
            table.add_row(label, "[dim]—[/dim]", f"${a.total:,.2f}", "[green]NEW[/green]")
        elif a is None:
            table.add_row(label, f"${b.total:,.2f}", "[dim]—[/dim]", "[red]REMOVED[/red]")
        else:
            table.add_row(
                label, f"${b.total:,.2f}", f"${a.total:,.2f}", _delta_cell(b.total, a.total)
            )

    console.print(table)
    console.print()
