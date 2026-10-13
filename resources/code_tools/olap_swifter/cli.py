"""
CLI interface for olap_swifter.
Schema workflow (schema/source/synth/etl/check/constellation) lives in schema_cli;
this module adds pipeline profiling, the reviewer UI, Playwright verification and OCR.
"""

import json

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from typing import Optional

from olap_swifter import schema_cli

app = typer.Typer(help="OLAP Swifter: Medical OLAP Engine, Clinical Digitizer & Deep Cube Slicer")
console = Console()
schema_cli.register(app)


def MedicalOLAPOrchestrator(*args, **kwargs):
    # Imported lazily so the schema workflow runs without the reviewer/Playwright stack.
    from olap_swifter.orchestrator import MedicalOLAPOrchestrator as _Orchestrator
    return _Orchestrator(*args, **kwargs)


@app.command()
def build(
    visits: int = typer.Option(500, help="Number of synthetic clinical visits to generate (capped at dummy_max_rows rows)"),
    csv: Optional[str] = typer.Option(None, "--csv", help="Path to input raw clinical CSV file"),
    schema: Optional[str] = typer.Option(None, help="Registered schema id (default: clinical_visit, or the matched schema for --csv)"),
    source: Optional[str] = typer.Option(None, help="Source name for --csv (use the dataset name)"),
    force: bool = typer.Option(False, "--force", help="Load despite failed gates (requires user agreement)"),
    as_json: bool = typer.Option(False, "--json", help="Print a counts-only summary for the orchestrating skill"),
):
    """Loads dummy data (or a CSV that fits a registered schema) and runs the full pipeline."""
    orch = MedicalOLAPOrchestrator()
    try:
        if csv:
            data_stats = orch.ingest_csv_dataset(csv, schema_id=schema, source=source, force=force)
        else:
            data_stats = orch.ingest_synthetic_dataset(num_visits=visits, schema_id=schema or "clinical_visit")
        res = orch.run_full_pipeline()
    except ValueError as e:
        from olap_swifter.feedback.worklog import log_step
        from olap_swifter.schema.registry import SchemaRegistry
        try:
            ds = SchemaRegistry().get(schema).dataset_name_for_source(source) if schema and source else None
        except KeyError:
            ds = None
        log_step("production_build" if csv else "dummy_build", False, ds, schema, note=str(e)[:160])
        if as_json:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            console.print(f"[bold red]✗ {e}[/bold red]")
        raise typer.Exit(code=1)

    val = res["semantic_validation"]
    from pathlib import Path
    from olap_swifter.feedback.worklog import log_step, save_build
    from olap_swifter.paths import route
    dummy = not csv or route("dummy_data").resolve() in Path(csv).resolve().parents
    save_build(res["review"])
    spec = orch.spec
    log_step("dummy_build" if dummy else "production_build", res["zero_variable_loss"] and val["all_passed"],
             spec.dataset_name_for_source(data_stats["source_id"]), spec.schema_id,
             note=data_stats["source_id"], checks=f"{val['passed']}/{val['total_tests']}")
    if as_json:
        # Counts only: no slice labels or values, which could expose small cells of real data.
        print(json.dumps({
            "ok": res["zero_variable_loss"] and val["all_passed"],
            "schema": res["schema"], "source_id": data_stats["source_id"], "main_fact": res["main_fact"],
            "visits": data_stats["num_visits"], "raw_rows": data_stats["raw_records_generated"],
            "table_counts": data_stats["table_counts"],
            "zero_variable_loss": res["zero_variable_loss"], "missing_variables": res["missing_variables"],
            "validation": {k: val[k] for k in ("total_tests", "passed", "failed", "all_passed")},
            "failed_checks": [{k: r[k] for k in ("check", "target", "expected", "actual")}
                              for r in val["details"] if r["severity"] == "error" and not r["passed"]],
            "join_paths": {t: i["number_of_joins"] for t, i in res["join_analysis"].items()},
            "deepest_slice_depth": (res["deepest_slice"] or {}).get("depth"),
            "reviewer_html": res["reviewer_html"],
        }, indent=2, default=str))
        if not (res["zero_variable_loss"] and val["all_passed"]):
            raise typer.Exit(code=1)
        return

    console.print(Panel.fit("[bold cyan]Building Medical OLAP Warehouse & Constellation Schemas[/bold cyan]"))
    console.print(f"[green]✓ Ingested {data_stats['num_visits']} visits ({data_stats['raw_records_generated']} clinical records)[/green]")
    console.print(f"[green]✓ Zero Variable Loss: {res['zero_variable_loss']} ({res['total_variables']} variables mapped)[/green]")
    console.print(f"[green]✓ Validation: {val['passed']}/{val['total_tests']} checks passed[/green]")

    # Table counts
    table = Table(title="OLAP Warehouse Table Inventory")
    table.add_column("Table Name", style="cyan")
    table.add_column("Row Count", style="magenta")
    for tbl, count in data_stats["table_counts"].items():
        table.add_row(tbl, str(count))
    console.print(table)


@app.command()
def profile(schema: str = typer.Option("clinical_visit", help="Registered schema id")):
    """Profiles join paths, digs out deepest cube slices, and measures component latency."""
    console.print(Panel.fit("[bold magenta]OLAP Join Path, Latency & Deep Cube Slicer Profiling[/bold magenta]"))
    orch = MedicalOLAPOrchestrator()
    orch.ingest_synthetic_dataset(num_visits=300, schema_id=schema)
    res = orch.run_full_pipeline()

    # Join paths
    join_table = Table(title=f"Join Distances from Main Fact ({res['main_fact']})")
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
    console.print(f"  • Fact Constellation: [cyan]{bm['constellation']['total_duration_ms']} ms[/cyan]")


@app.command()
def review(dataset: Optional[str] = typer.Option(None, help="Dataset to review (default: the latest one worked on)")):
    """Re-renders model_reviewer.html: schema art, process done so far, last build's fields and checks."""
    from olap_swifter.feedback.worklog import render_review
    console.print(f"[bold green]Reviewer:[/bold green] file://{render_review(dataset=dataset)}")


@app.command()
def verify_ui(switch_field: Optional[str] = typer.Option(None, help="Field to switch in UI"),
              target_table: Optional[str] = typer.Option(None, help="New target table")):
    """Runs Playwright headless browser to verify the model reviewer UI and field switching."""
    from olap_swifter.feedback.browser_runner import PlaywrightFeedbackRunner
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


@app.command()
def ocr(
    image: str = typer.Argument(..., help="Path to clinical form image or scan"),
    cursive: bool = typer.Option(True, help="Enable tail-end cursive & handwriting recognition"),
    anonymize: bool = typer.Option(True, help="Enable Presidio patient PII & contact redaction")
):
    """Executes OCR layer: Tesseract + Tail-end Cursive HTR + Presidio privacy + Structured field extraction."""
    from ocr_tools.tesseract_runner import TesseractRunner
    console.print(Panel.fit(f"[bold cyan]Clinical OCR Extraction & De-Identification[/bold cyan]\nImage: [yellow]{image}[/yellow]"))
    runner = TesseractRunner()
    result = runner.process_full_pipeline(image, run_cursive=cursive, anonymize=anonymize)

    console.print(f"[bold green]Engine:[/bold green] {result['engine']}")

    # Presidio De-Identified Text
    console.print(Panel(result["anonymized_text"], title="[bold blue]Presidio De-Identified Text (Facility Preserved)[/bold blue]"))

    # Extracted Fields Table
    table = Table(title="Extracted Clinical Fields (Visit Spine)")
    table.add_column("Field", style="cyan")
    table.add_column("Extracted Value", style="magenta")
    for k, v in result["fields"].items():
        table.add_row(k, str(v))
    console.print(table)


if __name__ == "__main__":
    app()

