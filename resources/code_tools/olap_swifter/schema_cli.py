"""
Schema workflow commands: describe a dataset, match it to the registry, model or
extend a schema, prove it on dummy data, then load real data — returning only
counts and pass/fail. Every command takes --json for the orchestrating skill.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import typer
import yaml
from rich.console import Console
from rich.table import Table

from olap_swifter.feedback.worklog import STAGES, log_step
from olap_swifter.paths import make_workspace, route, workspace_path, zip_workspace
from olap_swifter.schema.constellation_view import build_view, render_markdown
from olap_swifter.schema.describe import describe
from olap_swifter.schema.extender import attach_dataset, extend_schema, placement_of, require_user_placement
from olap_swifter.schema.matcher import match_registry
from olap_swifter.schema.materializer import WarehouseLoader, source_id_for
from olap_swifter.schema.checks import run_checks
from olap_swifter.schema.profile import (
    load_description, load_dictionary, load_eda, merge_profile_and_dictionary, profile_csv, write_eda,
)
from olap_swifter.schema.registry import SchemaRegistry, default_inputs_dir
from olap_swifter.schema.readers import ACCEPTED, data_entries, entry_stem
from olap_swifter.schema.vocabulary import VocabularyError, crosswalk_path, label_counts, merge_crosswalk
from olap_swifter.schema.spec import SchemaSpecError, load_spec_file
from olap_swifter.schema.synthetic import write_synthetic_dataset

console = Console()
schema_app = typer.Typer(help="Registered schemas: list, show, validate, register, extend, attach.")
source_app = typer.Typer(help="Datasets: build an EDA, match a dataset against the registry.")

CATALOGUE_HELP = "Data catalogue / dictionary: a CSV or YAML file, or a folder of them (merged). Required."
EDA_HELP = "The dataset's EDA (Exploratory Data Analysis) file, or a folder with one EDA per data file (`source eda` writes them). Required."
MAIN_FACT_HELP = "The dataset's own main fact table (from the dataset concept's main_event); defaults to the schema's."
DESCRIPTION_HELP = "The dataset concept: inputs/<dataset>/dataset_concept.yaml (template: resources/templates/dataset_concept.yaml)."


def _emit(obj: Any, as_json: bool, render=None):
    if as_json or render is None:
        print(json.dumps(obj, indent=2, default=str))
    else:
        render(obj)


def _fail(msg: str, as_json: bool):
    _emit({"ok": False, "error": msg}, as_json, lambda o: console.print(f"[bold red]✗ {msg}[/bold red]"))
    raise typer.Exit(code=1)


def _require(path: str, label: str, as_json: bool):
    if not path or not Path(path).exists():
        _fail(f"{label} not found: {path!r}", as_json)


def _dataset_from(path: str) -> Optional[str]:
    """Dataset name of a path inside the inputs folder (<inputs>/<name>/...)."""
    try:
        return Path(path).resolve().relative_to(default_inputs_dir().resolve()).parts[0]
    except (ValueError, IndexError):
        return None


def _db(db: Optional[str], synthetic: bool) -> str:
    return db or str(workspace_path("warehouse_dummy" if synthetic else "warehouse"))


def _dataset_variables(catalogue: str, eda: str) -> List[Dict[str, Any]]:
    return merge_profile_and_dictionary(load_eda(eda), load_dictionary(catalogue))


def _print_checks(summary: Dict[str, Any], title: str):
    t = Table(title=f"{title}: {summary['passed']}/{summary['total']} passed")
    for col in ("check", "target", "expected", "actual", "detail"):
        t.add_column(col)
    for r in summary["results"]:
        style = "green" if r["passed"] else "red"
        if r["severity"] == "info":
            style = "cyan"
        t.add_row(f"[{style}]{r['check']}[/{style}]", r["target"], str(r["expected"]), str(r["actual"]),
                  r["detail"])
    console.print(t)


# ---------------- schema ----------------

@schema_app.command("list")
def schema_list(as_json: bool = typer.Option(False, "--json")):
    """Lists registered schemas."""
    rows = SchemaRegistry().list()

    def render(rows):
        t = Table(title="Registered schemas")
        for c in ("schema_id", "title", "main_fact", "tables", "columns", "datasets"):
            t.add_column(c)
        for r in rows:
            t.add_row(r["schema_id"], r["title"], r["main_fact"], str(r["tables"]), str(r["columns"]),
                      ", ".join(r["datasets"]))
        console.print(t)
    _emit(rows, as_json, render)


@schema_app.command("show")
def schema_show(schema_id: str, as_json: bool = typer.Option(False, "--json")):
    """Prints a schema's YAML (or its JSON metadata)."""
    spec = SchemaRegistry().get(schema_id)
    if as_json:
        _emit(spec.to_yaml_dict(), True)
    else:
        console.print(spec.to_yaml())


@schema_app.command("validate")
def schema_validate(path: str, as_json: bool = typer.Option(False, "--json")):
    """Validates a schema YAML written by the skill, including conflicts with reused tables."""
    try:
        spec = load_spec_file(path)
    except (SchemaSpecError, yaml.YAMLError) as e:
        _fail(str(e), as_json)
    conflicts = SchemaRegistry().table_conflicts(spec)
    reg = SchemaRegistry()
    owners = reg.table_owners()
    reused = {t: owners[t] for t in spec.tables if t in owners and spec.schema_id not in owners[t]}
    out = {"ok": not conflicts, "schema": spec.schema_id, "tables": len(spec.tables),
           "columns": len(spec.all_columns()), "reused_tables": reused, "conflicts": conflicts}
    _emit(out, as_json, lambda o: console.print(o))
    if conflicts:
        raise typer.Exit(code=1)


def _attach_and_save(spec, dataset: str, catalogue: str, eda: str, description: Optional[str],
                     main_fact: Optional[str]) -> Dict[str, Any]:
    reg = SchemaRegistry()
    if description:
        todo = load_description(description)["skill_to_fill"]
        if todo:
            raise SchemaSpecError(f"model these description fields before attaching: {', '.join(todo)}")
    variables = _dataset_variables(catalogue, eda)
    tagged = attach_dataset(spec, dataset, variables, catalogue, eda, description, main_fact)
    path = reg.save(spec)
    view = build_view(reg, highlight=tagged["dataset"])
    return {"ok": True, "schema": spec.schema_id, "path": str(path), "dataset": tagged["dataset"],
            "dataset_main_fact": spec.datasets[tagged["dataset"]]["main_fact"],
            "dataset_columns": tagged["columns"],
            "constellation_tables_with_dataset": view["highlighted_tables"]}


@schema_app.command("register")
def schema_register(
    path: str,
    dataset: str = typer.Option(..., help="Dataset this new schema is modelled from"),
    catalogue: str = typer.Option(..., help=CATALOGUE_HELP),
    eda: str = typer.Option(..., help=EDA_HELP),
    description: Optional[str] = typer.Option(None, "--concept", "--description", help=DESCRIPTION_HELP),
    main_fact: Optional[str] = typer.Option(None, help=MAIN_FACT_HELP),
    as_json: bool = typer.Option(False, "--json"),
):
    """Registers a brand-new schema (decision `new_schema`) and attaches its dataset."""
    _require(catalogue, "catalogue", as_json)
    _require(eda, "EDA", as_json)
    reg = SchemaRegistry()
    try:
        if placement_of(description) == "own_mart":  # its own mart shares no table with another schema
            new = load_spec_file(path)
            owners = reg.table_owners()
            shared = sorted(t for t in new.tables if t in owners and new.schema_id not in owners[t])
            if shared:
                raise SchemaSpecError(
                    f"the user chose its own data mart (placement: own_mart), but this schema reuses "
                    f"{', '.join(shared)} from other schemas; give the mart its own tables "
                    f"(e.g. {shared[0]}_{dataset}) or ask the user again")
        reg.register(Path(path))
        spec = reg.get(load_spec_file(path).schema_id)
        out = _attach_and_save(spec, dataset, catalogue, eda, description, main_fact)
    except (SchemaSpecError, yaml.YAMLError) as e:
        _fail(str(e), as_json)
    log_step("model", True, out["dataset"], out["schema"], decision="new_schema", columns=len(out["dataset_columns"]))
    _emit(out, as_json, lambda o: console.print(o))


@schema_app.command("extend")
def schema_extend(
    patch: str,
    dataset: str = typer.Option(..., help="Dataset that motivated the extension"),
    catalogue: str = typer.Option(..., help=CATALOGUE_HELP),
    eda: str = typer.Option(..., help=EDA_HELP),
    description: Optional[str] = typer.Option(None, "--concept", "--description", help=DESCRIPTION_HELP),
    main_fact: Optional[str] = typer.Option(None, help=MAIN_FACT_HELP),
    as_json: bool = typer.Option(False, "--json"),
):
    """Applies an additive patch (decision `extend`) in place and attaches the dataset."""
    _require(catalogue, "catalogue", as_json)
    _require(eda, "EDA", as_json)
    try:
        spec = extend_schema(patch, concept=description)
        out = _attach_and_save(spec, dataset, catalogue, eda, description, main_fact)
    except (SchemaSpecError, KeyError, yaml.YAMLError) as e:
        _fail(str(e), as_json)
    out["changelog"] = spec.changelog[-1]
    log_step("model", True, out["dataset"], out["schema"], decision="extend", columns=len(out["dataset_columns"]))
    _emit(out, as_json, lambda o: console.print(o))


@schema_app.command("attach")
def schema_attach(
    schema_id: str,
    dataset: str = typer.Option(...),
    catalogue: str = typer.Option(..., help=CATALOGUE_HELP),
    eda: str = typer.Option(..., help=EDA_HELP),
    description: Optional[str] = typer.Option(None, "--concept", "--description", help=DESCRIPTION_HELP),
    main_fact: Optional[str] = typer.Option(None, help=MAIN_FACT_HELP),
    as_json: bool = typer.Option(False, "--json"),
):
    """Attaches a dataset that fits a schema as-is (decision `fit`)."""
    _require(catalogue, "catalogue", as_json)
    _require(eda, "EDA", as_json)
    try:
        require_user_placement(description, schema_id, "attach the dataset to")
        out = _attach_and_save(SchemaRegistry().get(schema_id), dataset, catalogue, eda, description, main_fact)
    except (SchemaSpecError, KeyError) as e:
        _fail(str(e), as_json)
    log_step("model", True, out["dataset"], out["schema"], decision="fit", columns=len(out["dataset_columns"]))
    _emit(out, as_json, lambda o: console.print(o))


# ---------------- source ----------------

@source_app.command("eda")
def source_eda(
    csv_path: str,
    out: str = typer.Option(..., help="EDA file to write (.yaml, or .csv for the minimal CSV form); "
                                      "a folder when CSV_PATH is a folder (one EDA per file)"),
    as_csv: bool = typer.Option(False, "--csv", help="With a folder: write minimal CSV EDAs instead of YAML"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Writes aggregate-only EDA (counts, ranges, category counts with small cells hidden) for a CSV or Excel file, or a folder of them."""
    _require(csv_path, "CSV", as_json)
    cfg = SchemaRegistry().config()
    src = Path(csv_path)
    ext = ".csv" if as_csv else ".yaml"
    targets = [(str(src / e), Path(out) / f"{entry_stem(e)}{ext}") for e in data_entries(src)] if src.is_dir() \
        else [(str(src), Path(out))]
    if not targets:
        _fail(f"no data files ({', '.join(ACCEPTED['data'])}) in {csv_path}", as_json)
    written = []
    for data_file, eda_path in targets:
        eda = profile_csv(str(data_file), max_categories=int(cfg["eda_max_categories"]),
                          min_category_count=int(cfg["eda_min_category_count"]))
        written.append({"eda": write_eda(eda, str(eda_path)), "rows": eda["row_count"],
                        "variables": eda["column_count"]})
    _emit({"ok": True, "files": written}, as_json, lambda o: console.print(o))


@source_app.command("describe")
def source_describe(
    name: str = typer.Argument(..., help="Dataset name = its folder under the workspace's inputs/"),
    study: str = typer.Option("", help="2-5 sentences: population, setting, purpose"),
    main_question: str = typer.Option("", help="The primary analysis the warehouse must answer"),
    one_row_is: str = typer.Option("", help="What one row of the export represents"),
    main_event: str = typer.Option("", help="The event counted or measured (becomes the main fact)"),
    placement: str = typer.Option("", help="The user's choice if it overlaps a built schema: extend | own_mart | ask_me"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Writes inputs/<name>/dataset_concept.yaml (and the EDA if missing). Same as `describe(...)`."""
    try:
        extra = {"placement": placement} if placement else {}
        result = describe(name, study, main_question, one_row_is, main_event, quiet=True, **extra)
    except (FileNotFoundError, ValueError) as e:
        _fail(str(e), as_json)
    log_step("describe", not result["missing_required"], name, data_files=len(result["data_files"]),
             missing_required=len(result["missing_required"]))
    _emit(result, as_json, lambda o: console.print(o))


@source_app.command("values")
def source_values(
    dataset: str = typer.Argument(..., help="Dataset name (its folder under results_workspace/inputs/)"),
    variable: str = typer.Argument(..., help="Vocabulary variable to list, e.g. ward"),
    by: Optional[str] = typer.Option(None, help="Also list where each label is seen, e.g. hospital_code"),
    crosswalk: Optional[str] = typer.Option(None, help="Merge new labels into results_workspace/schemas/crosswalks/<name>.yaml"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Distinct labels of a vocabulary variable (ward, unit, specimen...) with counts, for a crosswalk."""
    folder = default_inputs_dir() / dataset
    files = [str(folder / "data" / e) for e in data_entries(folder / "data")]
    if not files:
        _fail(f"no data files in {folder / 'data'}", as_json)
    try:
        out = label_counts(files, variable, by, str(folder / "dataset_concept.yaml"))
    except VocabularyError as e:
        _fail(str(e), as_json)
    if crosswalk:
        out["crosswalk"] = merge_crosswalk(crosswalk_path(SchemaRegistry().dir, crosswalk), crosswalk,
                                           variable, out["labels"])
        log_step("crosswalk", out["crosswalk"]["unmapped"] == 0, dataset, note=f"{variable} -> {crosswalk}",
                 labels=out["distinct"], unmapped=out["crosswalk"]["unmapped"])

    def render(o):
        t = Table(title=f"{o['variable']}: {o['distinct']} labels in {o['rows_with_value']} rows")
        t.add_column("label")
        t.add_column("rows")
        if o["by"]:
            t.add_column(o["by"])
        for l in o["labels"]:
            t.add_row(l["value"], str(l["rows"]), *([", ".join(l["seen_at"])] if o["by"] else []))
        console.print(t)
        if "crosswalk" in o:
            console.print(o["crosswalk"])
    _emit(out, as_json, render)


@source_app.command("match")
def source_match(
    catalogue: str = typer.Option(..., help=CATALOGUE_HELP),
    eda: str = typer.Option(..., help=EDA_HELP),
    description: Optional[str] = typer.Option(None, "--concept", "--description", help=DESCRIPTION_HELP),
    as_json: bool = typer.Option(False, "--json"),
):
    """Scores the dataset against every registered schema: fit, extend or new_schema."""
    _require(catalogue, "catalogue", as_json)
    _require(eda, "EDA", as_json)
    result = match_registry(_dataset_variables(catalogue, eda))
    warnings = []
    if description:
        _require(description, "description", as_json)
        desc = load_description(description)
        if desc["missing_required"]:
            warnings.append("description is missing required fields: " + ", ".join(desc["missing_required"]))
        result["skill_to_fill"] = desc["skill_to_fill"]
    else:
        warnings.append("no dataset description given: the main fact, grain and visit definition are guesses")
    result["warnings"] = warnings
    log_step("match", True, _dataset_from(catalogue), result["best_schema"],
             recommendation=result["recommendation"])

    def render(r):
        console.print(f"[bold]Recommendation:[/bold] {r['recommendation']}  (best: {r['best_schema']}, "
                      f"threshold {r['threshold']:.0%} difference)")
        t = Table(title="Candidates")
        for c in ("schema", "decision", "difference", "unmatched", "blockers"):
            t.add_column(c)
        for c in r["candidates"]:
            t.add_row(c["schema"], c["decision"], f"{c['difference']:.0%}", ", ".join(c["unmatched"]),
                      "\n".join(c["blockers"]))
        console.print(t)
        if r["tied_with"]:
            console.print(f"[yellow]! tie with {', '.join(r['tied_with'])}: choose using the dataset description[/yellow]")
        for w in r["warnings"]:
            console.print(f"[yellow]! {w}[/yellow]")
    _emit(result, as_json, render)


# ---------------- run ----------------

def synth(
    schema_id: str,
    out_dir: Optional[str] = typer.Option(None, help="Directory for the dummy CSV, catalogue and EDA (default: workspace dummy/<schema>/)"),
    visits: int = typer.Option(200, help="Number of dummy visits (fewer if the row cap is reached)"),
    max_rows: Optional[int] = typer.Option(None, help="Row cap; defaults to dummy_max_rows (400). Raise only when a test needs volume"),
    seed: int = typer.Option(7),
    as_json: bool = typer.Option(False, "--json"),
):
    """Generates capped dummy data (plus its catalogue and EDA) from a schema. Prints counts, never rows."""
    cfg = SchemaRegistry().config()
    out_dir = out_dir or str(workspace_path("dummy_data") / schema_id)
    out = write_synthetic_dataset(SchemaRegistry().get(schema_id), out_dir, n_visits=visits, seed=seed,
                                  max_categories=int(cfg["eda_max_categories"]),
                                  min_category_count=int(cfg["eda_min_category_count"]),
                                  max_rows=max_rows or int(cfg["dummy_max_rows"]))
    log_step("dummy_data", True, schema=schema_id, rows=out["rows"], visits=out["visits"])
    _emit(out, as_json, lambda o: console.print(o))


def etl(
    schema_id: str,
    csv_path: str,
    source: str = typer.Option(..., help="Name for this data source (becomes its source_id)"),
    db: Optional[str] = typer.Option(None, help="DuckDB warehouse file (default: the workspace warehouse, or the dummy one with --synthetic)"),
    synthetic: bool = typer.Option(False, help="Mark the source as dummy data"),
    force: bool = typer.Option(False, help="Load even if preflight gates fail"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Stages, gates, loads and checks one source. Prints counts only, never rows."""
    _require(csv_path, "CSV", as_json)
    spec = SchemaRegistry().get(schema_id)
    conn = duckdb.connect(_db(db, synthetic))
    try:
        report = WarehouseLoader(conn).load(spec, csv_path, source, is_synthetic=synthetic, force=force)
    finally:
        conn.close()

    def render(r):
        console.print(f"[bold]{r['source_id']}[/bold] -> {r['schema']}: "
                      + ("[green]loaded[/green]" if r["loaded"] else f"[red]not loaded[/red] ({r['reason']})"))
        if r["unmapped_variables"]:
            console.print(f"[red]Unmapped variables:[/red] {', '.join(r['unmapped_variables'])}")
        _print_checks(r["preflight"], "Preflight gates")
        if r["loaded"]:
            t = Table(title="Tables")
            for c in ("table", "status", "rows / entities"):
                t.add_column(c)
            for x in r["tables"]:
                t.add_row(x["table"], x["status"], str(x.get("rows", x.get("entities_in_source", x.get("reason")))))
            console.print(t)
            _print_checks(r["postload"], "Post-load checks")
    failed = [r for part in ("preflight", "postload") for r in (report.get(part) or {}).get("results", [])
              if r["severity"] == "error" and not r["passed"]]
    log_step("dummy_load" if synthetic else "production_load",
             report["loaded"] and report.get("postload", {}).get("all_passed", False),
             spec.dataset_name_for_source(source), schema_id, note=source, failed_checks=len(failed),
             forced=bool(force))
    _emit(report, as_json, render)
    if not report["loaded"] or not report.get("postload", {}).get("all_passed", False):
        raise typer.Exit(code=1)


def check(
    schema_id: str,
    source: str = typer.Option(..., help="Source name or id that was loaded"),
    db: Optional[str] = typer.Option(None, help="Warehouse file (default: the workspace warehouse, or the dummy one with --synthetic)"),
    synthetic: bool = typer.Option(False, help="Check the dummy warehouse"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Re-runs every gate and post-load check for a loaded source."""
    spec = SchemaRegistry().get(schema_id)
    conn = duckdb.connect(_db(db, synthetic))
    try:
        out = run_checks(conn, spec, source_id_for(source))
    finally:
        conn.close()

    def render(o):
        _print_checks(o["preflight"], "Preflight gates")
        _print_checks(o["postload"], "Post-load checks")
    log_step("dummy_load" if synthetic else "production_load",
             out["preflight"]["all_passed"] and out["postload"]["all_passed"],
             spec.dataset_name_for_source(source), schema_id, note=f"re-check {source}")
    _emit(out, as_json, render)
    if not (out["preflight"]["all_passed"] and out["postload"]["all_passed"]):
        raise typer.Exit(code=1)


def constellation(
    highlight: Optional[str] = typer.Option(None, help="Dataset whose variables to highlight"),
    out: Optional[str] = typer.Option(None, help="Write the Markdown view here (default with --highlight: the dataset's workspace folder)"),
    as_json: bool = typer.Option(False, "--json"),
):
    """One view of every schema merged into the warehouse constellation, a dataset highlighted."""
    view = build_view(SchemaRegistry(), highlight=highlight)
    md = render_markdown(view)
    if highlight and not out:
        out = str(workspace_path("dataset_outputs") / highlight / "constellation.md")
    if highlight:
        log_step("report", True, highlight, tables=len(view["highlighted_tables"]),
                 columns=len(view["highlighted_columns"]))
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(md, encoding="utf-8")
    if as_json:
        _emit(view, True)
    else:
        console.print(md if not out else f"[green]✓ wrote {out}[/green] "
                      f"({len(view['highlighted_columns'])} highlighted columns)")


def log(
    stage: str = typer.Argument(..., help="One of: " + ", ".join(STAGES)),
    dataset: str = typer.Option(..., help="Dataset the step was for"),
    failed: bool = typer.Option(False, "--failed", help="The step failed"),
    note: str = typer.Option("", help="Short summary (counts only, never data values)"),
    tokens: Optional[int] = typer.Option(None, help="The agent's own token use so far, as the host reports it"),
    passed: Optional[int] = typer.Option(None, help="Tests passed"),
    total: Optional[int] = typer.Option(None, help="Tests run"),
):
    """Records a step the tools cannot see themselves (e.g. TEST.md sub-agent results) on the review page."""
    if stage not in STAGES:
        _fail(f"unknown stage {stage!r}; use one of {', '.join(STAGES)}", False)
    counts = {"passed": f"{passed}/{total}"} if passed is not None and total is not None else {}
    if tokens is not None:
        counts["tokens"] = tokens
    log_step(stage, not failed, dataset, note=note, **counts)
    console.print(f"[green]✓ logged {stage}[/green] -> {route('model_reviewer')}")


def workspace(
    dataset: Optional[str] = typer.Option(None, help="Also make this dataset's inputs folder"),
    zip_it: bool = typer.Option(False, "--zip", help="Zip the workspace for download (raw data/ exports left out)"),
    include_data: bool = typer.Option(False, help="With --zip: put the raw data/ exports in too"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Creates the results workspace (outside the skill) and its README; prints the paths to give the user."""
    try:
        out = make_workspace(dataset)
    except ValueError as e:
        _fail(str(e), as_json)
    if zip_it:
        out.update(zip_workspace(include_data=include_data))

    def render(o):
        say = lambda t: console.print(t, soft_wrap=True)  # noqa: E731  paths stay copyable
        say(f"[bold green]Results workspace:[/bold green] {o['workspace']}")
        say(f"  key (README): {o['readme']}")
        say(f"  review page:  {o['review_page']}")
        if "inputs" in o:
            say(f"  put the dataset in: {o['inputs']}")
        if "zip" in o:
            say(f"  zip: {o['zip']} ({o['files']} files)")
    _emit(out, as_json, render)


def register(app: typer.Typer):
    app.add_typer(schema_app, name="schema")
    app.command()(workspace)
    app.add_typer(source_app, name="source")
    app.command()(synth)
    app.command()(etl)
    app.command()(check)
    app.command()(constellation)
    app.command()(log)
