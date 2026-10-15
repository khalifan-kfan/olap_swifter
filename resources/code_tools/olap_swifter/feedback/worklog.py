"""
Process log and review page. Every tool step appends one line (counts only,
never data values) to the workspace process log and re-renders
model_reviewer.html, so the page always shows the work done so far.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from olap_swifter.paths import route, workspace_path

# The workflow in SKILL.md, in order: stage -> what the review page calls it.
STAGES = {
    "describe": "Inputs described",
    "match": "Matched against the registry",
    "model": "Schema fitted, extended or registered",
    "crosswalk": "Site vocabularies mapped",
    "dummy_data": "Dummy data generated",
    "dummy_load": "Dummy data loaded and checked",
    "dummy_build": "Dummy build",
    "dummy_tests": "TEST.md on dummy data",
    "production_load": "Production data loaded and checked",
    "production_build": "Production build",
    "production_tests": "TEST.md on production data",
    "report": "Constellation report",
}


def log_step(stage: str, ok: bool, dataset: Optional[str] = None, schema: Optional[str] = None,
             note: str = "", **counts: Any) -> None:
    """Records one step and refreshes the review page. Never fails the calling command."""
    try:
        entry = {"time": datetime.now().isoformat(timespec="seconds"), "stage": stage, "ok": bool(ok),
                 "dataset": dataset, "schema": schema, "note": note, "counts": counts}
        with open(workspace_path("process_log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        render_review()
    except Exception:
        pass


def read_log() -> List[Dict[str, Any]]:
    p = route("process_log")
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def save_build(payload: Dict[str, Any]) -> None:
    """Keeps the last build's field inventory and check results for the review page."""
    (workspace_path("review") / "last_build.json").write_text(json.dumps(payload, default=str), encoding="utf-8")


def _last_build() -> Dict[str, Any]:
    p = route("review") / "last_build.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def process_for(dataset: Optional[str], log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each stage with its status for the dataset: done, failed (latest attempt) or not yet."""
    schemas = {e["schema"] for e in log if e["dataset"] == dataset and e["schema"]}
    mine = [e for e in log if e["dataset"] == dataset or (e["dataset"] is None and e["schema"] in schemas)]
    out = []
    for stage, label in STAGES.items():
        runs = [e for e in mine if e["stage"] == stage]
        last = runs[-1] if runs else None
        out.append({"stage": stage, "label": label, "runs": len(runs),
                    "status": "todo" if not last else ("done" if last["ok"] else "failed"),
                    "time": last["time"] if last else "", "note": last["note"] if last else "",
                    "counts": last["counts"] if last else {}})
    return out


def render_review(build: Optional[Dict[str, Any]] = None, dataset: Optional[str] = None) -> str:
    """Writes model_reviewer.html from the last build, the process log and the registry."""
    from olap_swifter.feedback.interactive_app import generate_interactive_html
    from olap_swifter.feedback.schema_art import render_svg
    from olap_swifter.schema.constellation_view import build_view

    build = build or _last_build()
    log = read_log()
    if dataset is None:
        dataset = next((e["dataset"] for e in reversed(log) if e["dataset"]), None)
    view = build_view(highlight=dataset)
    return generate_interactive_html(
        fields=build.get("fields", []),
        validation_summary=build.get("validation", {}),
        join_analysis=build.get("join_analysis", {}),
        deep_slices=build.get("deep_slices", []),
        output_path=str(workspace_path("model_reviewer")),
        table_options=build.get("table_options"),
        schema_svg=render_svg(view),
        process=process_for(dataset, log),
        context={"dataset": dataset, "schema": build.get("schema"), "source": build.get("source_id"),
                 "highlighted_tables": len(view["highlighted_tables"]),
                 "highlighted_columns": len(view["highlighted_columns"])},
    )
