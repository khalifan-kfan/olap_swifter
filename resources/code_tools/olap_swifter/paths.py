"""
File routing. Every path the tools use comes from resources/ROUTES.yaml by
name. Skill paths are relative to the repo root ($OLAP_SWIFTER_ROOT, else the
nearest folder above the working directory that has resources/ROUTES.yaml,
else this repo). Workspace paths are relative to the results workspace, which
lives outside the skill: see workspace_root().
"""

import json
import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import yaml

ROUTES_FILE = Path("resources") / "ROUTES.yaml"


def _root_from_cwd() -> Optional[Path]:
    for d in (Path.cwd(), *Path.cwd().parents):
        if (d / ROUTES_FILE).exists():
            return d
    return None


def repo_root() -> Path:
    env = os.environ.get("OLAP_SWIFTER_ROOT")
    if env:
        return Path(env)
    return _root_from_cwd() or Path(__file__).resolve().parents[3]


@lru_cache(maxsize=None)
def _routes(root: str) -> Dict[str, Dict[str, Any]]:
    return yaml.safe_load((Path(root) / ROUTES_FILE).read_text(encoding="utf-8"))["routes"]


def routes() -> Dict[str, Dict[str, Any]]:
    return _routes(str(repo_root()))


def workspace_root() -> Path:
    """
    The results workspace, always outside the skill: $OLAP_SWIFTER_WORKSPACE;
    else `results_workspace/` in the folder the tools are run from; else (run
    from inside the skill) `results_workspace/` beside the skill's folder.
    """
    env = os.environ.get("OLAP_SWIFTER_WORKSPACE")
    if env:
        return Path(env).resolve()
    name = routes()["workspace"]["path"]
    skill, cwd = repo_root().resolve(), Path.cwd().resolve()
    if cwd == skill or skill in cwd.parents:
        return skill.parent / name
    return cwd / name


def _split_workspace(path: str) -> Tuple[bool, str]:
    prefix = routes()["workspace"]["path"]
    if path == prefix or path.startswith(prefix + "/"):
        return True, path[len(prefix):].lstrip("/")
    return False, path


def route(name: str) -> Path:
    """Absolute path of a named resource, e.g. route("warehouse")."""
    in_ws, rest = _split_workspace(routes()[name]["path"])
    if in_ws:
        return workspace_root() / rest if rest else workspace_root()
    return repo_root() / rest


def workspace_path(name: str) -> Path:
    """A workspace route, with its parent folder created and the workspace README.md refreshed."""
    p = route(name)
    (p if not p.suffix else p.parent).mkdir(parents=True, exist_ok=True)
    write_workspace_readme()
    return p


# The skeleton of one dataset's inputs folder.
DATASET_INPUT_FOLDERS = ("catalogue", "data", "eda", "codes", "documents")
CONCEPT_NAME = "dataset_concept.yaml"

STANDING_DECISIONS = [
    "**The skill never reads your data rows.** It models from the catalogue, the EDA (Exploratory Data "
    "Analysis: column types, missing values, ranges and category counts, no rows) and the dataset concept. "
    "Only the code tools open `inputs/<dataset>/data/`.",
    "**An EDA is made when one is missing.** Each data file without an EDA in `eda/` gets one: aggregates "
    "only, rare categories pooled so no one can be picked out. You can also give your own, or make one with "
    "ydata-profiling (see the repo's `HOW_TO_RUN.md`).",
    "**Dummy data first.** A schema is proven on dummy rows in `warehouse/warehouse_dummy.duckdb` before any "
    "real data loads into `warehouse/warehouse.duckdb`. The two never share a file.",
    "**Data is loaded as it is.** The warehouse copies the source values faithfully; problems are reported "
    "(review page, process log), not fixed.",
    "**Schemas live here, not in the repo.** They are built from your datasets, so they stay with them. The "
    "repo holds only the generic tools.",
    "**Nothing here is committed to git.**",
]


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _process_steps() -> Dict[str, List[Dict[str, Any]]]:
    """Process-log lines grouped by dataset."""
    steps: Dict[str, List[Dict[str, Any]]] = {}
    for entry in _read_jsonl(route("process_log")):
        steps.setdefault(entry.get("dataset") or "(no dataset)", []).append(entry)
    return steps


def _link(ws: Path, p: Path, label: Optional[str] = None) -> str:
    rel = p.relative_to(ws).as_posix()
    return f"[{label or rel}]({quote(rel)})" if p.exists() else f"`{rel}` (not made yet)"


def _dataset_sections(ws: Path) -> List[str]:
    inputs, outputs = route("inputs"), route("dataset_outputs")
    steps = _process_steps()
    names = sorted({p.parent.name for p in inputs.glob(f"*/{CONCEPT_NAME}")} | (set(steps) - {"(no dataset)"}))
    if not names:
        return ["No dataset yet. Put one in `inputs/<name>/` (layout below) and start the skill.", ""]
    lines = ["| dataset | schema | decision | last step |", "| :--- | :--- | :--- | :--- |"]
    concepts = {n: _read_yaml(inputs / n / CONCEPT_NAME) for n in names}
    for n in names:
        rel = concepts[n].get("relationship_to_existing") or {}
        last = steps.get(n, [{}])[-1]
        last_txt = f"{last.get('stage')} ({'ok' if last.get('ok') else 'failed'}, {last.get('time')})" if last else "-"
        lines.append(f"| {n} | {rel.get('schema') or '-'} | {rel.get('decision') or '-'} | {last_txt} |")
    lines.append("")
    for n in names:
        c = concepts[n]
        visit, unit = c.get("visit") or {}, c.get("unit_of_analysis") or {}
        rel, ent = c.get("relationship_to_existing") or {}, c.get("entities") or {}
        lines += [f"### {n}", ""]
        facts = [("Main question", (c.get("study") or {}).get("main_question")),
                 ("One row is", unit.get("one_row_is")), ("Main event (main fact)", unit.get("main_event")),
                 ("Schema decision", f"{rel.get('decision')} -> `{rel.get('schema')}`" if rel.get("schema") else ""),
                 ("Visit", f"{visit.get('definition') or ''} (identifier: `{visit.get('identifier')}`)"
                  if visit.get("identifier") else ""),
                 ("Patient identifier", f"`{ent['patient_identifier']}`" if ent.get("patient_identifier") else ""),
                 ("Known issues", c.get("known_issues"))]
        lines += [f"- **{k}:** {v}" for k, v in facts if v]
        done: Dict[str, List[int]] = {}
        for st in steps.get(n, []):
            ran = done.setdefault(st.get("stage") or "?", [0, 0])
            ran[0] += 1
            ran[1] += not st.get("ok")
        if done:
            lines.append("- **Steps done:** " + ", ".join(
                f"{k} x{r[0]}" + (f" ({r[1]} failed)" if r[1] else "") for k, r in done.items())
                + " (full timeline on the review page)")
        links = [_link(ws, inputs / n / CONCEPT_NAME, "dataset concept"),
                 _link(ws, outputs / n / "constellation.md", "constellation")]
        if (outputs / n / "patches").is_dir():
            links.append(_link(ws, outputs / n / "patches", "schema patches"))
        lines += [f"- **Files:** {', '.join(links)}", ""]
    return lines


# What each data issue means and how it could be cleaned. The warehouse holds
# the source as recorded; cleaning is the data owner's decision.
CLEANING = {
    "value_range": "values outside the schema's plausible range (typos, sentinels such as -1)",
    "allowed_values": "values outside the vocabulary (spelling or case variants, retired codes)",
    "required_nulls": "a required variable left empty",
    "entity_consistency": "one entity recorded with different values (only the first is on the dimension)",
}


def _data_issue_sections(ws: Path) -> List[str]:
    """Every dataset's data issues from its last load of each source, counts only."""
    import json
    outputs = route("dataset_outputs")
    files = sorted(outputs.glob("*/data_issues/*.json")) if outputs.is_dir() else []
    lines: List[str] = []
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        kind = "dummy" if rec.get("synthetic") else "real"
        issues = rec.get("issues") or []
        lines += [f"### {rec.get('dataset')} — source `{rec.get('source_id')}` ({kind} data, {rec.get('recorded_at')})", ""]
        if not issues:
            lines += ["No data issues.", ""]
            continue
        lines += ["| issue | where | rows | what it is |", "| :--- | :--- | ---: | :--- |"]
        for i in issues:
            lines.append(f"| {i.get('check')} | `{i.get('target')}` | {i.get('actual')} | "
                         f"{CLEANING.get(i.get('check'), i.get('detail') or '')} |")
        lines.append("")
    if not lines:
        return ["None recorded yet.", ""]
    return (["The source is transferred as it is: these values are in the warehouse exactly as recorded. "
             "They are listed for the data owner to clean at the source, not fixed here. Counts only; "
             "each load of a source rewrites its entry.", ""] + lines)


CHARS_PER_TOKEN = 4

# The skill's documents the agent reads, by route.
SKILL_DOCUMENTS = ("skill", "terms", "how_to_run", "tests", "dataset_concept_template")


def estimate_tokens(chars: int) -> int:
    """Estimated tokens for this many characters (characters / 4, rounded up)."""
    return -(-chars // CHARS_PER_TOKEN)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _token_weight() -> List[str]:
    lines = ["How heavy this skill is, in estimated tokens (characters / 4; the host's own count is the",
             "exact one).", "", "**Loading the skill** (the documents the agent reads)", "",
             "| document | ≈ tokens |", "| :--- | ---: |"]
    total = 0
    for name in SKILL_DOCUMENTS:
        p = route(name)
        if p.exists():
            n = estimate_tokens(len(p.read_text(encoding="utf-8")))
            total += n
            lines.append(f"| `{routes()[name]['path']}` | {n:,} |")
    lines += [f"| **total** | **{total:,}** |", "",
              "**Tool output read by the agent** (everything an `olap-swifter` command printed)", ""]
    runs: Dict[str, List[int]] = {}
    for e in _read_jsonl(route("token_log")):
        r = runs.setdefault(e.get("command") or "?", [0, 0])
        r[0] += 1
        r[1] += int(e.get("tokens") or 0)
    if runs:
        lines += ["| command | runs | ≈ tokens |", "| :--- | ---: | ---: |"]
        lines += [f"| `{c}` | {r[0]} | {r[1]:,} |" for c, r in sorted(runs.items(), key=lambda kv: -kv[1][1])]
        lines.append(f"| **total** | **{sum(r[0] for r in runs.values())}** | "
                     f"**{sum(r[1] for r in runs.values()):,}** |")
    else:
        lines.append("No command run yet.")
    reported = {}
    for e in _process_steps().items():
        for st in e[1]:
            if "tokens" in (st.get("counts") or {}):
                reported[e[0]] = (st["counts"]["tokens"], st.get("time"))
    lines += ["", "**The agent's own total** (its reading, reasoning and replies; only the host counts these)", ""]
    if reported:
        lines += [f"- {d}: {int(n):,} tokens (reported {t})" for d, (n, t) in sorted(reported.items())]
    else:
        lines.append("Not reported. The agent records it, when the host shows it, with "
                     "`olap-swifter log <stage> --dataset <name> --tokens <n>`.")
    return lines + [""]


def _structure(ws: Path) -> List[str]:
    """The workspace tree from ROUTES.yaml, folders implied by a route included."""
    entries: Dict[str, str] = {}
    for name, r in routes().items():
        in_ws, rel = _split_workspace(r["path"])
        if not in_ws or name in ("workspace", "workspace_readme"):
            continue
        parts = rel.split("/")
        for i in range(1, len(parts)):
            entries.setdefault("/".join(parts[:i]) + "/", "")
        entries["/".join(parts) + ("" if Path(rel).suffix else "/")] = r["what"]
        if name == "inputs":
            entries[rel + "/<dataset>/"] = ("One per dataset: " + ", ".join(f"{d}/" for d in DATASET_INPUT_FOLDERS)
                                           + f", {CONCEPT_NAME}")
    lines = ["```", f"{ws.name}/", f"{'  README.md':<40}This file: the key to everything below"]
    for key in sorted(entries, key=lambda k: k.rstrip("/").split("/")):
        depth = key.rstrip("/").count("/")
        shown = key.rstrip("/").split("/")[-1] + ("/" if key.endswith("/") else "")
        lines.append(f"{'  ' * (depth + 1) + shown:<40}{entries[key]}".rstrip())
    lines += ["```", ""]
    return lines


def write_workspace_readme() -> Path:
    """results_workspace/README.md: the results key. Where to start, what each folder is, the decisions made."""
    ws = route("workspace")
    ws.mkdir(parents=True, exist_ok=True)
    lines = ["# Results workspace", "",
             "Everything olap_swifter made for your datasets. Never committed to git. The tools rewrite",
             "this file after every step, so edits here are lost.", "",
             "## Start here", "",
             f"- **Review the model:** {_link(ws, route('model_reviewer'), 'review/model_reviewer.html')}. "
             "Open it in a browser: schema art, every step done so far, the field mapping and the checks.",
             f"- **The warehouse:** {_link(ws, route('warehouse'))} (real data) and "
             f"{_link(ws, route('warehouse_dummy'))} (dummy data). Browse them read-only with `olap-swifter view`.",
             "- **Add a dataset:** `olap-swifter workspace --dataset <name>` makes its inputs folder.", "",
             "## Datasets and the decisions made for them", ""]
    lines += _dataset_sections(ws)
    lines += ["## Data issues for cleaning", ""] + _data_issue_sections(ws)
    lines += ["## Token weight", ""] + _token_weight()
    lines += ["## Standing decisions", ""] + [f"- {d}" for d in STANDING_DECISIONS] + [""]
    lines += ["## Folder structure", ""] + _structure(ws)
    lines += ["## Files here now", "", "```"]
    for p in sorted(ws.rglob("*")):
        if p.is_file() and p.name not in ("README.md", ".DS_Store") and "__pycache__" not in p.parts:
            lines.append(f"{p.relative_to(ws).as_posix()}  ({p.stat().st_size:,} bytes)")
    lines += ["```", ""]
    out = route("workspace_readme")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def make_workspace(dataset: Optional[str] = None) -> Dict[str, str]:
    """
    Creates the results workspace (every route folder), and a dataset's inputs
    folder when `dataset` is given; writes the README. Returns the paths to hand
    the user.
    """
    for name, r in routes().items():
        if _split_workspace(r["path"])[0]:
            p = route(name)
            (p if not p.suffix else p.parent).mkdir(parents=True, exist_ok=True)
    out = {"workspace": str(route("workspace")), "readme": str(write_workspace_readme()),
           "review_page": str(route("model_reviewer"))}
    if dataset:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", dataset):
            raise ValueError(f"dataset name must be letters, digits, _ or -: {dataset!r}")
        folder = route("inputs") / dataset
        for d in DATASET_INPUT_FOLDERS:
            (folder / d).mkdir(parents=True, exist_ok=True)
        out["inputs"] = str(folder)
        out["readme"] = str(write_workspace_readme())
    return out


def zip_workspace(out: Optional[str] = None, include_data: bool = False) -> Dict[str, Any]:
    """
    Zips the results workspace for download (when the run has no folder the
    user keeps, e.g. a chat). Raw exports in inputs/<dataset>/data/ and the
    private staging databases (raw rows and the key map to source identifiers)
    are left out unless `include_data`: the user already has them. The
    warehouses go in; they hold the loaded data under generated keys.
    """
    ws = route("workspace")
    write_workspace_readme()
    target = Path(out) if out else ws.parent / f"{ws.name}.zip"
    files, skipped = 0, 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(ws.rglob("*")):
            rel = p.relative_to(ws)
            if not p.is_file() or p.name == ".DS_Store" or "__pycache__" in rel.parts or p.resolve() == target.resolve():
                continue
            if not include_data and ((len(rel.parts) > 2 and rel.parts[0] == "inputs" and rel.parts[2] == "data")
                                     or p.name.endswith("_staging.duckdb")):
                skipped += 1
                continue
            zf.write(p, Path(ws.name) / rel)
            files += 1
    return {"zip": str(target), "files": files, "raw_data_files_left_out": skipped,
            "bytes": target.stat().st_size}
