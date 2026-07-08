"""Interactive Textual TUI for cloudslayer — split-panel navigation of analyze and plan output."""

from __future__ import annotations

from rich import box as rbox
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
)


def _effort_color(effort: str) -> str:
    return {"None": "green", "Low": "green", "Medium": "yellow", "High": "red"}.get(effort, "white")


def _risk_color(risk: str) -> str:
    return {"Low": "green", "Medium": "yellow", "High": "red"}.get(risk, "white")


def _savings_bar(value: float, max_value: float, width: int = 10) -> str:
    """Proportional bar of filled/empty blocks, e.g. ▰▰▰▰▱▱▱▱▱▱."""
    if max_value <= 0:
        return "▱" * width
    filled = max(1, round(value / max_value * width))
    filled = min(filled, width)
    return "▰" * filled + "▱" * (width - filled)


def _normalize(pairs: list) -> list[tuple]:
    """Normalize 2/3/4-tuples to (spec, results, current_provider, label)."""
    out = []
    for item in pairs:
        t = tuple(item)
        if len(t) == 2:
            out.append((*t, "", ""))
        elif len(t) == 3:
            out.append((*t, ""))
        else:
            out.append(t[:4])
    return out


def _vs_cell(
    result_total: float,
    cheapest_total: float,
    current_total: float,
    is_current: bool,
    use_current: bool,
) -> str:
    if use_current:
        if is_current:
            return "[bold yellow]← you are here[/bold yellow]"
        diff = current_total - result_total
        pct = (diff / current_total * 100) if current_total > 0 else 0
        if diff > 0.01:
            return f"[green]save ${diff:,.2f}/mo ({pct:.0f}%)[/green]"
        diff2 = result_total - current_total
        pct2 = (diff2 / current_total * 100) if current_total > 0 else 0
        return f"[red]+${diff2:,.2f} more ({pct2:.0f}%)[/red]"
    else:
        if result_total == cheapest_total:
            return "[green]cheapest[/green]"
        diff = result_total - cheapest_total
        pct = (diff / cheapest_total * 100) if cheapest_total > 0 else 0
        return f"[red]+${diff:,.2f} (+{pct:.0f}%)[/red]"


class _ThemeMixin:
    """Shared light/dark theme toggle."""

    def action_toggle_theme(self) -> None:
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"


# ── Analyze TUI ────────────────────────────────────────────────────────────────


class AnalyzeTUI(_ThemeMixin, App[None]):
    """Split-panel TUI: strategy list on the left, details on the right."""

    CSS = """
    Screen { layout: vertical; }
    #summary {
        height: auto;
        padding: 0 2;
        background: $boost;
        border-bottom: solid $primary;
    }
    #body { layout: horizontal; height: 1fr; }
    #nav {
        width: 42;
        height: 100%;
        border-right: solid $primary-darken-2;
        background: $panel;
    }
    #detail { width: 1fr; padding: 1 2; overflow: auto scroll; }
    ListItem { padding: 1 1; border-bottom: solid $surface-lighten-2; border-left: thick $panel; }
    ListItem.--highlight { background: $accent 20%; border-left: thick $accent; }
    ListItem.tier-header {
        padding: 0 1;
        background: $boost;
        border-bottom: solid $primary-darken-2;
        border-left: thick $boost;
    }
    ListItem.tier-header.--highlight { background: $boost; border-left: thick $boost; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit", show=False),
        Binding("j", "nav_down", "Down", show=False),
        Binding("k", "nav_up", "Up", show=False),
        Binding("1", "jump_tier(1)", "Wins"),
        Binding("2", "jump_tier(2)", "Commit"),
        Binding("3", "jump_tier(3)", "Migrate"),
        Binding("t", "toggle_theme", "Theme"),
    ]

    _TIER_LABELS = {
        1: "⚡ Immediate wins",
        2: "○ Commitment purchases",
        3: "↗ Major migrations",
    }

    def __init__(self, resources: list, strategies: list, source_label: str = "") -> None:
        super().__init__()
        self._resources = resources
        self._strategies = strategies
        self._source_label = source_label
        self._current_total = sum(r.monthly_cost for r in resources)
        self._max_savings = max((s.savings_mo for s in strategies), default=0.0)
        self._idx_map: list[int | None] = []  # ListView index → strategy index (None = separator)
        self._last_nav_idx = 0  # last non-header index, for header skip direction

    def _build_sidebar_items(self) -> tuple[list[ListItem], list[int | None]]:
        items: list[ListItem] = []
        idx_map: list[int | None] = []
        current_tier: int | None = None
        for strat_idx, s in enumerate(self._strategies):
            if s.priority != current_tier:
                current_tier = s.priority
                label = self._TIER_LABELS.get(s.priority, f"Priority {s.priority}")
                items.append(
                    ListItem(
                        Label(f"[dim]{label}[/dim]", markup=True),
                        classes="tier-header",
                        disabled=True,
                    )
                )
                idx_map.append(None)
            items.append(self._strategy_item(strat_idx, s))
            idx_map.append(strat_idx)
        return items, idx_map

    def _summary_text(self) -> str:
        best = self._strategies[0] if self._strategies else None
        dominant = sum(1 for s in self._strategies if s.is_dominant)
        parts = [f"[bold]${self._current_total:,.2f}/mo[/bold] current spend"]
        if best:
            top = max(self._strategies, key=lambda s: s.savings_mo)
            parts.append(
                f"best move saves [bold green]${top.savings_mo:,.2f}/mo[/bold green] ({top.name})"
            )
        parts.append(
            f"{len(self._strategies)} strategies"
            + (f" · [green]{dominant} ★ no trade-off[/green]" if dominant else "")
        )
        return "  ·  ".join(parts)

    def compose(self) -> ComposeResult:
        sidebar_items, self._idx_map = self._build_sidebar_items()
        yield Header(show_clock=False)
        yield Static(self._summary_text(), id="summary", markup=True)
        with Horizontal(id="body"):
            yield ListView(*sidebar_items, id="nav")
            with ScrollableContainer(id="detail"):
                yield Static("", id="panel")
        yield Footer()

    def on_mount(self) -> None:
        n = len(self._resources)
        label = f"  ·  {self._source_label}" if self._source_label else ""
        self.title = (
            f"cloudslayer analyze{label}"
            f"  ·  {n} resource{'s' if n != 1 else ''}"
            f"  ·  ${self._current_total:,.2f}/mo current"
        )
        nav = self.query_one("#nav", ListView)
        nav.focus()
        first_lv_idx = next((i for i, s in enumerate(self._idx_map) if s is not None), None)
        if first_lv_idx is not None:
            nav.index = first_lv_idx
            self._show(self._idx_map[first_lv_idx])

    def _strategy_item(self, i: int, s) -> ListItem:
        ec = _effort_color(s.effort)
        rc = _risk_color(s.risk)
        dominant = "  [bold green]★[/bold green]" if s.is_dominant else ""
        bar = _savings_bar(s.savings_mo, self._max_savings)
        be = ""
        if s.migration_cost_est > 0 and s.break_even_months > 0:
            be = f"  ·  BE {s.break_even_months:.0f}mo"
        text = (
            f"[bold]{i + 1}. {s.name}[/bold]{dominant}\n"
            f"   [green]{bar}[/green]  [green]${s.savings_mo:,.0f}/mo ({s.savings_pct:.0f}%)[/green]\n"
            f"   [{ec}]{s.effort}[/{ec}] effort · [{rc}]{s.risk}[/{rc}] risk[dim]{be}[/dim]"
        )
        return ListItem(Label(text, markup=True))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv = event.list_view
        lv_idx = lv.index
        if lv_idx is None or lv_idx >= len(self._idx_map):
            return
        strat_idx = self._idx_map[lv_idx]
        if strat_idx is None:
            # Landed on a tier header — hop over it in the direction of travel
            direction = 1 if lv_idx >= self._last_nav_idx else -1
            new = lv_idx + direction
            while 0 <= new < len(self._idx_map) and self._idx_map[new] is None:
                new += direction
            if not (0 <= new < len(self._idx_map)):
                new = self._last_nav_idx  # edge of the list: stay where we were
            if new != lv_idx:
                lv.index = new
            return
        self._last_nav_idx = lv_idx
        # Keep the tier header visible when sitting on the first strategy of a tier
        if lv_idx > 0 and self._idx_map[lv_idx - 1] is None:
            try:
                list(lv.query(ListItem))[lv_idx - 1].scroll_visible(animate=False)
            except Exception:
                pass
        self._show(strat_idx)

    def action_nav_down(self) -> None:
        self.query_one("#nav", ListView).action_cursor_down()

    def action_nav_up(self) -> None:
        self.query_one("#nav", ListView).action_cursor_up()

    def action_jump_tier(self, tier: int) -> None:
        for lv_idx, strat_idx in enumerate(self._idx_map):
            if strat_idx is not None and self._strategies[strat_idx].priority == tier:
                self.query_one("#nav", ListView).index = lv_idx
                return

    def _show(self, idx: int) -> None:
        s = self._strategies[idx]
        ec = _effort_color(s.effort)
        rc = _risk_color(s.risk)
        overhead = f"  [dim]+${s.overhead_mo:.2f}/mo overhead[/dim]" if s.overhead_mo > 0 else ""
        dominant = "  [bold green]★ no trade-off[/bold green]" if s.is_dominant else ""
        be = ""
        if s.migration_cost_est > 0 and s.break_even_months > 0:
            be = f"\n[dim]Break-even: {s.break_even_months:.0f} months  (one-time migration ~${s.migration_cost_est:,.0f})[/dim]"

        tbl = Table(box=rbox.SIMPLE, show_header=True, padding=(0, 1), show_edge=False)
        tbl.add_column("Resource", style="dim", min_width=14)
        tbl.add_column("Current", min_width=20)
        tbl.add_column("Alternative", min_width=20)
        tbl.add_column("Note", style="dim", min_width=16)
        tbl.add_column("Save/mo", justify="right", min_width=10)
        for item in s.items:
            save = item.from_cost - item.to_cost
            tbl.add_row(
                item.resource_name,
                item.from_label,
                item.to_label,
                item.note,
                f"[green]${save:,.2f}[/green]" if save > 0.005 else "[dim]—[/dim]",
            )

        caveats = "\n".join(f"  [yellow]⚠[/yellow]  [dim]{c}[/dim]" for c in s.caveats)

        self.query_one("#panel", Static).update(
            Group(
                Text.from_markup(
                    f"\n[bold cyan]{s.name}[/bold cyan]{dominant}\n"
                    f"[bold green]Save ${s.savings_mo:,.2f}/mo ({s.savings_pct:.0f}%)[/bold green]"
                    f"    Effort [{ec}]{s.effort}[/{ec}]    Risk [{rc}]{s.risk}[/{rc}]{overhead}{be}\n\n"
                    f"[dim]{s.pitch}[/dim]\n"
                ),
                tbl,
                Text.from_markup(f"\n{caveats}") if caveats else Text(""),
            )
        )


# ── Plan / Compare TUI ─────────────────────────────────────────────────────────


class PlanTUI(_ThemeMixin, App[None]):
    """Tabbed TUI for plan/compare: resource list on left, provider table on right."""

    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    TabPane { layout: horizontal; height: 1fr; padding: 0; }
    .res-list {
        width: 26;
        height: 100%;
        border-right: solid $primary-darken-2;
        background: $panel;
    }
    .detail-pane {
        width: 1fr;
        padding: 1 2;
        overflow: auto scroll;
    }
    #totalbar {
        height: auto;
        padding: 0 2;
        background: $boost;
        border-top: solid $primary;
    }
    ListItem { padding: 0 2; border-left: thick $panel; }
    ListItem.--highlight { background: $accent 20%; border-left: thick $accent; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit", show=False),
        Binding("j", "nav_down", "Down", show=False),
        Binding("k", "nav_up", "Up", show=False),
        Binding("t", "toggle_theme", "Theme"),
    ]

    def __init__(
        self,
        all_storage: list,
        all_compute: list,
        all_database: list,
        all_serverless: list | None = None,
        title: str = "cloudslayer plan",
    ) -> None:
        super().__init__()
        self._data: dict[str, list[tuple]] = {
            "storage": _normalize(all_storage),
            "compute": _normalize(all_compute),
            "database": _normalize(all_database),
            "serverless": _normalize(all_serverless or []),
        }
        self._app_title = title

    def _cheapest_label(self, kind: str, spec, results) -> str:
        get_cost = (lambda r: r.monthly_cost) if kind == "serverless" else (lambda r: r.total)
        cheapest = min(results, key=get_cost) if results else None
        price = f"  [dim]${get_cost(cheapest):,.0f}[/dim]" if cheapest else ""
        return f"{spec.name}{price}"

    def _total_summary(self) -> str:
        cheapest = expensive = 0.0
        n = 0
        for kind, items in self._data.items():
            get_cost = (lambda r: r.monthly_cost) if kind == "serverless" else (lambda r: r.total)
            for _, results, *_ in items:
                if not results:
                    continue
                costs = [get_cost(r) for r in results]
                cheapest += min(costs)
                expensive += max(costs)
                n += 1
        if n == 0:
            return ""
        return (
            f"{n} resources  ·  cheapest combination [bold green]${cheapest:,.2f}/mo[/bold green]"
            f" (${cheapest * 12:,.0f}/yr)"
            f"  ·  most expensive [red]${expensive:,.2f}/mo[/red]"
            f"  ·  max spread [bold]${expensive - cheapest:,.2f}/mo[/bold]"
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent():
            for kind, label in (
                ("storage", "Storage"),
                ("compute", "Compute"),
                ("database", "Database"),
                ("serverless", "Serverless"),
            ):
                items = self._data[kind]
                if not items:
                    continue
                with TabPane(f"{label} ({len(items)})", id=f"tab-{kind}"):
                    with Horizontal():
                        yield ListView(
                            *[
                                ListItem(
                                    Label(self._cheapest_label(kind, spec, results), markup=True)
                                )
                                for spec, results, *_ in items
                            ],
                            id=f"nav-{kind}",
                            classes="res-list",
                        )
                        with ScrollableContainer(classes="detail-pane", id=f"detail-{kind}"):
                            yield Static("", id=f"panel-{kind}")
        summary = self._total_summary()
        if summary:
            yield Static(summary, id="totalbar", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = self._app_title
        for kind in ("storage", "compute", "database", "serverless"):
            if self._data[kind]:
                try:
                    self.query_one(f"#nav-{kind}", ListView).focus()
                    self._show(kind, 0)
                except Exception:
                    pass
                break

    def action_nav_down(self) -> None:
        if isinstance(self.focused, ListView):
            self.focused.action_cursor_down()

    def action_nav_up(self) -> None:
        if isinstance(self.focused, ListView):
            self.focused.action_cursor_up()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv_id = event.list_view.id or ""
        for kind in ("storage", "compute", "database", "serverless"):
            if lv_id == f"nav-{kind}":
                idx = event.list_view.index
                if idx is not None:
                    self._show(kind, idx)
                break

    def on_tabbed_content_tab_activated(self, event) -> None:
        pane = getattr(event, "pane", None)
        pane_id = pane.id if pane else ""
        for kind in ("storage", "compute", "database", "serverless"):
            if pane_id == f"tab-{kind}" and self._data[kind]:
                try:
                    lv = self.query_one(f"#nav-{kind}", ListView)
                    lv.focus()
                    self._show(kind, lv.index or 0)
                except Exception:
                    pass
                break

    def _show(self, kind: str, idx: int) -> None:
        items = self._data[kind]
        if idx >= len(items):
            return
        spec, results, current_provider, instance_label = items[idx]
        panel = self.query_one(f"#panel-{kind}", Static)
        if kind == "storage":
            panel.update(self._storage_view(spec, results, current_provider))
        elif kind == "compute":
            panel.update(self._compute_view(spec, results, current_provider, instance_label))
        elif kind == "database":
            panel.update(self._database_view(spec, results, current_provider, instance_label))
        elif kind == "serverless":
            panel.update(self._serverless_view(spec, results, current_provider))

    # ── per-type table builders ────────────────────────────────────────────────

    def _storage_view(self, spec, results, current_provider: str) -> Group:
        sorted_r = sorted(results, key=lambda r: r.total)
        cheapest = sorted_r[0] if sorted_r else None
        cur = next((r for r in sorted_r if r.provider == current_provider), None)
        use_cur = cur is not None
        cur_total = cur.total if cur else 0.0

        usage = (
            f"{spec.storage_gb:,.0f} GB  ·  {spec.get_requests:,} GET  ·  "
            f"{spec.put_requests:,} PUT  ·  {spec.egress_gb:,.0f} GB egress"
        )
        tbl = Table(
            title=f'[bold]object_storage[/bold] [cyan]"{spec.name}"[/cyan]\n[dim]{usage}[/dim]',
            box=rbox.ROUNDED,
            header_style="bold",
            expand=True,
        )
        tbl.add_column("Provider", min_width=24, no_wrap=True)
        tbl.add_column("Storage", justify="right", min_width=9)
        tbl.add_column("Requests", justify="right", min_width=9)
        tbl.add_column("Egress", justify="right", min_width=9)
        tbl.add_column("Monthly", justify="right", min_width=10, style="bold")
        tbl.add_column(
            "vs Current" if use_cur else "vs Cheapest", justify="right", min_width=22, no_wrap=True
        )

        for r in sorted_r:
            is_cur = r.provider == current_provider
            lbl = (
                f"[yellow]→ {r.display_name} (current)[/yellow]"
                if is_cur
                else f"[green]★ {r.display_name}[/green]"
                if r is cheapest
                else r.display_name
            )
            tbl.add_row(
                lbl,
                f"${r.storage_cost:,.2f}",
                f"${r.request_cost:,.2f}",
                f"${r.egress_cost:,.2f}",
                f"${r.total:,.2f}",
                _vs_cell(r.total, cheapest.total if cheapest else 0, cur_total, is_cur, use_cur),
            )

        return Group(tbl, self._footer_text(sorted_r, cheapest, cur, use_cur, "display_name", None))

    def _compute_view(self, spec, results, current_provider: str, instance_label: str) -> Group:
        sorted_r = sorted(results, key=lambda r: r.total)
        cheapest = sorted_r[0] if sorted_r else None
        cur = next((r for r in sorted_r if r.provider == current_provider), None)
        use_cur = cur is not None
        cur_total = cur.total if cur else 0.0

        subtitle = f"{spec.vcpu} vCPU · {spec.memory_gb:.0f} GB RAM"
        if instance_label:
            subtitle += f"  [dim](from {instance_label})[/dim]"
        elif not use_cur:
            subtitle += " (minimum)"

        tbl = Table(
            title=f'[bold]compute[/bold] [cyan]"{spec.name}"[/cyan]\n[dim]{subtitle}[/dim]',
            box=rbox.ROUNDED,
            header_style="bold",
            expand=True,
        )
        tbl.add_column("Provider", min_width=24, no_wrap=True)
        tbl.add_column("Instance", min_width=16, no_wrap=True)
        tbl.add_column("vCPU", justify="right", min_width=5)
        tbl.add_column("RAM", justify="right", min_width=7)
        tbl.add_column("Monthly", justify="right", min_width=10, style="bold")
        tbl.add_column(
            "vs Current" if use_cur else "vs Cheapest", justify="right", min_width=22, no_wrap=True
        )

        for r in sorted_r:
            is_cur = r.provider == current_provider
            lbl = (
                f"[yellow]→ {r.display_name} (current)[/yellow]"
                if is_cur
                else f"[green]★ {r.display_name}[/green]"
                if r is cheapest
                else r.display_name
            )
            tbl.add_row(
                lbl,
                r.instance_name,
                str(r.instance_vcpu),
                f"{r.instance_memory_gb:.0f} GB",
                f"${r.total:,.2f}",
                _vs_cell(r.total, cheapest.total if cheapest else 0, cur_total, is_cur, use_cur),
            )

        extra = cheapest.instance_name if cheapest else ""
        return Group(
            tbl, self._footer_text(sorted_r, cheapest, cur, use_cur, "display_name", extra)
        )

    def _database_view(self, spec, results, current_provider: str, instance_label: str) -> Group:
        sorted_r = sorted(results, key=lambda r: r.total)
        cheapest = sorted_r[0] if sorted_r else None
        cur = next((r for r in sorted_r if r.provider == current_provider), None)
        use_cur = cur is not None
        cur_total = cur.total if cur else 0.0

        subtitle = f"{spec.vcpu} vCPU · {spec.memory_gb:.0f} GB RAM · {spec.storage_gb:.0f} GB · {spec.engine}"
        if instance_label:
            subtitle += f"  [dim](from {instance_label})[/dim]"

        tbl = Table(
            title=f'[bold]database[/bold] [cyan]"{spec.name}"[/cyan]\n[dim]{subtitle}[/dim]',
            box=rbox.ROUNDED,
            header_style="bold",
            expand=True,
        )
        tbl.add_column("Provider", min_width=18, no_wrap=True)
        tbl.add_column("Plan", min_width=20, no_wrap=True)
        tbl.add_column("vCPU", justify="right", min_width=5)
        tbl.add_column("RAM", justify="right", min_width=8)
        tbl.add_column("Instance", justify="right", min_width=9)
        tbl.add_column("Storage", justify="right", min_width=9)
        tbl.add_column("Monthly", justify="right", min_width=10, style="bold")
        tbl.add_column(
            "vs Current" if use_cur else "vs Cheapest", justify="right", min_width=22, no_wrap=True
        )

        for r in sorted_r:
            is_cur = r.provider == current_provider
            lbl = (
                f"[yellow]→ {r.display_name} (current)[/yellow]"
                if is_cur
                else f"[green]★ {r.display_name}[/green]"
                if r is cheapest
                else r.display_name
            )
            tbl.add_row(
                lbl,
                r.plan_name,
                str(r.plan_vcpu),
                f"{r.plan_memory_gb:.1f} GB",
                f"${r.instance_cost:,.2f}",
                f"${r.storage_cost:,.2f}",
                f"${r.total:,.2f}",
                _vs_cell(r.total, cheapest.total if cheapest else 0, cur_total, is_cur, use_cur),
            )

        extra = cheapest.plan_name if cheapest else ""
        return Group(
            tbl, self._footer_text(sorted_r, cheapest, cur, use_cur, "display_name", extra)
        )

    def _serverless_view(self, spec, results, current_provider: str) -> Group:
        sorted_r = sorted(results, key=lambda r: r.monthly_cost)
        cheapest = sorted_r[0] if sorted_r else None
        cur = next((r for r in sorted_r if r.provider == current_provider), None)
        use_cur = cur is not None
        cur_total = cur.monthly_cost if cur else 0.0

        subtitle = (
            f"{spec.invocations_per_month:,} invocations/mo  ·  "
            f"{spec.avg_duration_ms:.0f}ms avg  ·  {spec.memory_mb} MB"
        )
        tbl = Table(
            title=f'[bold]serverless[/bold] [cyan]"{spec.name}"[/cyan]\n[dim]{subtitle}[/dim]',
            box=rbox.ROUNDED,
            header_style="bold",
            expand=True,
        )
        tbl.add_column("Provider", min_width=24, no_wrap=True)
        tbl.add_column("Monthly", justify="right", min_width=10, style="bold")
        tbl.add_column("Per 1M Req", justify="right", min_width=12)
        tbl.add_column("Notes", min_width=28)
        tbl.add_column(
            "vs Current" if use_cur else "vs Cheapest", justify="right", min_width=22, no_wrap=True
        )

        for r in sorted_r:
            is_cur = r.provider == current_provider
            lbl = (
                f"[yellow]→ {r.display_name} (current)[/yellow]"
                if is_cur
                else f"[green]★ {r.display_name}[/green]"
                if r is cheapest
                else r.display_name
            )
            tbl.add_row(
                lbl,
                f"${r.monthly_cost:,.4f}",
                f"${r.per_million_requests:,.4f}",
                r.notes,
                _vs_cell(
                    r.monthly_cost,
                    cheapest.monthly_cost if cheapest else 0,
                    cur_total,
                    is_cur,
                    use_cur,
                ),
            )

        return Group(
            tbl,
            self._footer_text(
                sorted_r, cheapest, cur, use_cur, "display_name", None, use_monthly=True
            ),
        )

    def _footer_text(
        self,
        sorted_r: list,
        cheapest,
        cur,
        use_cur: bool,
        name_attr: str,
        plan_attr: str | None,
        use_monthly: bool = False,
    ) -> Text:
        if not sorted_r or not cheapest:
            return Text("")
        get_cost = (lambda r: r.monthly_cost) if use_monthly else (lambda r: r.total)
        lines = []
        if getattr(cheapest, "notes", ""):
            lines.append(f"[dim]★  {cheapest.notes}[/dim]")
        plan_str = (
            f" ({getattr(cheapest, plan_attr)})"
            if plan_attr and getattr(cheapest, plan_attr, "")
            else ""
        )
        name = getattr(cheapest, name_attr, "")
        if use_cur and cur and cur is not cheapest:
            savings = get_cost(cur) - get_cost(cheapest)
            lines.append(
                f"\n[bold]Switch:[/bold] [green]{name}[/green]{plan_str} saves "
                f"[bold green]${savings:,.2f}/mo[/bold green]"
            )
        elif len(sorted_r) > 1 and not use_cur:
            savings = get_cost(sorted_r[-1]) - get_cost(cheapest)
            lines.append(
                f"\n[bold]Recommendation:[/bold] [green]{name}[/green]{plan_str} saves "
                f"[bold]${savings:,.2f}/mo[/bold] vs most expensive"
            )
        return Text.from_markup("\n".join(lines)) if lines else Text("")
