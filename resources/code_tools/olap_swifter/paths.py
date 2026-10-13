"""
File routing. Every path the tools use comes from resources/ROUTES.yaml by
name, relative to the repo root ($OLAP_SWIFTER_ROOT, else the nearest folder
above the working directory that has resources/ROUTES.yaml, else this repo).
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

ROUTES_FILE = Path("resources") / "ROUTES.yaml"


def repo_root() -> Path:
    env = os.environ.get("OLAP_SWIFTER_ROOT")
    if env:
        return Path(env)
    for d in (Path.cwd(), *Path.cwd().parents):
        if (d / ROUTES_FILE).exists():
            return d
    return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=None)
def _routes(root: str) -> Dict[str, Dict[str, Any]]:
    return yaml.safe_load((Path(root) / ROUTES_FILE).read_text(encoding="utf-8"))["routes"]


def routes() -> Dict[str, Dict[str, Any]]:
    return _routes(str(repo_root()))


def route(name: str) -> Path:
    """Absolute path of a named resource, e.g. route("warehouse")."""
    return repo_root() / routes()[name]["path"]


def workspace_path(name: str) -> Path:
    """A workspace route, with its parent folder created and the workspace INDEX.md refreshed."""
    p = route(name)
    (p if not p.suffix else p.parent).mkdir(parents=True, exist_ok=True)
    write_workspace_index()
    return p


def write_workspace_index() -> Path:
    """scratch_workspace/INDEX.md: what each dumped file is, and what is there now."""
    root, ws = repo_root(), route("workspace")
    ws.mkdir(parents=True, exist_ok=True)
    lines = ["# Workspace index", "",
             "Git-ignored dumping folder for everything a run writes. Rewritten by the tools;",
             "the routes come from `resources/ROUTES.yaml`.", "",
             "| route | path | what it is / how to reuse it |", "| :--- | :--- | :--- |"]
    for name, r in routes().items():
        if r["path"].startswith(ws.name) and name != "workspace":
            lines.append(f"| `{name}` | `{r['path']}` | {r['what']} |")
    lines += ["", "## Here now", "", "```"]
    for p in sorted(ws.rglob("*")):
        if p.is_file() and p.name != "INDEX.md":
            lines.append(f"{p.relative_to(root)}  ({p.stat().st_size:,} bytes)")
    lines += ["```", ""]
    out = ws / "INDEX.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
