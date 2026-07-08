from __future__ import annotations

import asyncio
from io import StringIO

from rich.console import Console
from textual.widgets import Static

from cloudslayer.models import ComputeResult, ComputeSpec
from cloudslayer.strategies import AnalysisResource
from cloudslayer.tui import AnalyzeTUI, PlanTUI


def _fallback_result() -> ComputeResult:
    return ComputeResult(
        provider="aws_ec2",
        display_name="AWS EC2",
        instance_name="t3.medium",
        instance_vcpu=2,
        instance_memory_gb=4,
        price_per_month=30.37,
        price_source="fallback",
        source_url="https://aws.amazon.com/ec2/pricing/on-demand/",
    )


def test_plan_tui_keeps_pricing_warning_and_source_visible():
    async def scenario() -> None:
        spec = ComputeSpec("api", 2, 4)
        app = PlanTUI(
            [],
            [(spec, [_fallback_result()], "azure_vm", "Standard_B2s")],
            [],
            title="pricing test",
            pricing_warnings=[("Azure VM", "exact current SKU was unavailable")],
        )
        async with app.run_test(size=(180, 50)) as pilot:
            await pilot.pause()
            warning = app.query_one("#pricing-warning", Static)
            assert "Fallback pricing is present" in str(warning.content)
            assert "Azure VM omitted" in str(warning.content)

            panel = app.query_one("#panel-compute", Static)
            output = StringIO()
            Console(file=output, width=220, color_system=None).print(panel.content)
            rendered = output.getvalue()
            assert "fallback" in rendered
            assert "Current-provider baseline unavailable" in rendered
            assert "https://aws.amazon.com/ec2/pricing/on-demand/" in rendered

    asyncio.run(scenario())


def test_analyze_tui_labels_savings_as_modeled():
    async def scenario() -> None:
        resource = AnalysisResource(
            name="api",
            service="compute",
            current_provider="aws_ec2",
            monthly_cost=30.37,
            compute_spec=ComputeSpec("api", 2, 4),
            instance_type="t3.medium",
            price_source="fallback",
        )
        app = AnalyzeTUI([resource], [], source_label="test")
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            warning = app.query_one("#pricing-warning", Static)
            content = str(warning.content)
            assert "Modeled estimates" in content
            assert "Baseline pricing sources" in content
            assert "fallback" in content

    asyncio.run(scenario())
