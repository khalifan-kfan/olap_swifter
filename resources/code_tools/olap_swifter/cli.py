"""
CLI interface for olap_swifter.
Provides commands for dynamic modeling, validation, profiling, and Playwright verification.
"""

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from typing import Optional

from olap_swifter.orchestrator import MedicalOLAPOrchestrator
from olap_swifter.feedback.browser_runner import PlaywrightFeedbackRunner

app = typer.Typer(help="OLAP Swifter: Medical OLAP Engine, Clinical Digitizer & Deep Cube Slicer")
console = Console()


@app.command()
def build(
    visits: int = typer.Option(500, help="Number of synthetic clinical visits to generate"),
    csv: Optional[str] = typer.Option(None, "--csv", help="Path to input raw clinical CSV file")
):
    """Builds Star, Snowflake, and Constellation OLAP schemas and verifies zero variable loss."""
    console.print(Panel.fit("[bold cyan]Building Medical OLAP Warehouse & Constellation Schemas[/bold cyan]"))
    orch = MedicalOLAPOrchestrator()
    with console.status("Ingesting clinical records and building relational spine..."):
        if csv:
            data_stats = orch.ingest_csv_dataset(csv)
        else:
            data_stats = orch.ingest_synthetic_dataset(num_visits=visits)
        res = orch.run_full_pipeline()

    console.print(f"[green]✓ Ingested {data_stats['num_visits']} visits ({data_stats['raw_records_generated']} clinical records)[/green]")
    console.print(f"[green]✓ Zero Variable Loss: {res['zero_variable_loss']} ({res['total_variables']} variables mapped)[/green]")

    # Table counts
    table = Table(title="OLAP Warehouse Table Inventory")
    table.add_column("Table Name", style="cyan")
    table.add_column("Row Count", style="magenta")
    for tbl, count in data_stats["table_counts"].items():
        table.add_row(tbl, str(count))
    console.print(table)


@app.command()
def profile():
    """Profiles join paths, digs out deepest cube slices, and measures component latency."""
    console.print(Panel.fit("[bold magenta]OLAP Join Path, Latency & Deep Cube Slicer Profiling[/bold magenta]"))
    orch = MedicalOLAPOrchestrator()
    orch.ingest_synthetic_dataset(num_visits=300)
    res = orch.run_full_pipeline()

    # Join paths
    join_table = Table(title="Join Distances from Central Fact (fact_encounters)")
    join_table.add_column("Destination Table", style="cyan")
    join_table.add_column("Number of Joins", style="green")
    join_table.add_column("Shortest Join Path", style="yellow")

    for dest, info in res["join_analysis"].items():
        join_table.add_row(dest, str(info["number_of_joins"]), " ➔ ".join(info["path"]))
    console.print(join_table)

    # Deepest slices
    if res["deepest_slice"]:
        ds = res["deepest_slice"]
        console.print(Panel(
            f"[bold green]Deepest Slices Found (Depth {ds['depth']}):[/bold green]\n"
            f"Dimensions: [cyan]{' × '.join(ds['dimensions'])}[/cyan]\n"
            f"Populated Cells: {ds['non_empty_cells']} | Total Volume: {ds['total_volume']}\n"
            f"Avg Cell Size: {ds['avg_cell_size']}",
            title="Deepest Cube Slice"
        ))

    # Benchmark
    bm = res["benchmark"]
    console.print(f"\n[bold]Latency Benchmark ({bm['label']}):[/bold]")
    console.print(f"  • Star Schema: [cyan]{bm['star']['total_duration_ms']} ms[/cyan]")
    console.print(f"  • Snowflake Schema: [cyan]{bm['snowflake']['total_duration_ms']} ms[/cyan]")
    console.print(f"  • Fact Constellation: [cyan]{bm['constellation']['total_duration_ms']} ms[/cyan]")
    console.print(f"  • Fastest Model: [bold green]{bm['fastest'].upper()}[/bold green]")


@app.command()
def review():
    """Generates and displays the path to the interactive model reviewer HTML."""
    orch = MedicalOLAPOrchestrator()
    orch.ingest_synthetic_dataset(num_visits=200)
    res = orch.run_full_pipeline()
    console.print(f"[bold green]Interactive Reviewer generated:[/bold green] file://{res['reviewer_html']}")


@app.command()
def verify_ui(switch_field: Optional[str] = typer.Option(None, help="Field to switch in UI"),
              target_table: Optional[str] = typer.Option(None, help="New target table")):
    """Runs Playwright headless browser to verify the model reviewer UI and field switching."""
    console.print("[cyan]Running Playwright automated browser verification...[/cyan]")
    runner = PlaywrightFeedbackRunner()
    result = runner.run_sync(field_to_switch=switch_field, new_target_table=target_table)
    if result["success"]:
        console.print(f"[bold green]✓ Playwright Verification Succeeded![/bold green]")
        console.print(f"  • Rendered Fields: {result['field_count_rendered']}")
        console.print(f"  • Field Switched: {result['field_switched']}")
        console.print(f"  • Snapshot saved: {result['screenshot_saved']}")
    else:
        console.print(f"[yellow]Playwright run notice: {result.get('error')}[/yellow]")
        if "hint" in result:
            console.print(f"  Hint: {result['hint']}")


if __name__ == "__main__":
    app()
