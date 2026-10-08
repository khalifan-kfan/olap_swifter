"""
Constellation View.
Merges every registered schema into the one warehouse picture they build
together (tables are shared by name) and highlights the variables a given
dataset feeds, so it is clear where a dataset sits inside the constellation.
"""

from typing import Any, Dict, List, Optional

from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.schema.spec import normalize_name

HIGHLIGHT = "★"


def build_view(registry: Optional[SchemaRegistry] = None, highlight: Optional[str] = None) -> Dict[str, Any]:
    registry = registry or SchemaRegistry()
    ds = normalize_name(highlight) if highlight else None
    tables: Dict[str, Dict[str, Any]] = {}
    rels = set()
    spines = set()
    for spec in registry.all():
        spines.add(spec.spine_table)
        for t in spec.tables.values():
            entry = tables.setdefault(t.name, {"name": t.name, "kind": t.kind, "key": t.key,
                                              "schemas": [], "columns": {}, "main_fact_of": []})
            entry["schemas"].append(spec.schema_id)
            if spec.main_fact == t.name:
                entry["main_fact_of"].append(spec.schema_id)
            for ds_name, ds_entry in spec.datasets.items():
                if ds_entry.get("main_fact") == t.name and ds_entry.get("main_fact") != spec.main_fact:
                    entry["main_fact_of"].append(f"dataset {ds_name}")
            for c in spec.table_columns(t):
                inherited = c.table != t.name  # a key brought in through a link
                col = entry["columns"].setdefault(c.name, {"name": c.name, "type": c.type,
                                                           "role": "link" if inherited else c.role,
                                                           "datasets": set(), "inherited": inherited})
                col["datasets"].update(c.datasets)
        for r in spec.relationships():
            rels.add((r["from"], r["to"], r["key"]))

    for t in tables.values():
        cols = list(t["columns"].values())
        for c in cols:
            c["datasets"] = sorted(c["datasets"])
        # A table belongs to the dataset only through its own columns, not inherited join keys.
        t["highlighted"] = bool(ds) and any(ds in c["datasets"] for c in cols if not c["inherited"])
        for c in cols:
            c["highlighted"] = bool(ds and ds in c["datasets"]) and (t["highlighted"] or not c["inherited"])
            del c["inherited"]
        t["columns"] = cols

    ordered = sorted(tables.values(), key=lambda t: (t["kind"] != "dimension", t["name"] not in spines, t["name"]))
    highlighted_cols = [f"{t['name']}.{c['name']}" for t in ordered for c in t["columns"] if c["highlighted"]]
    return {
        "highlight": ds,
        "spines": sorted(spines),
        "tables": ordered,
        "relationships": [{"from": a, "to": b, "key": k} for a, b, k in sorted(rels)],
        "highlighted_columns": highlighted_cols,
        "highlighted_tables": [t["name"] for t in ordered if t["highlighted"]],
    }


def render_markdown(view: Dict[str, Any]) -> str:
    ds = view["highlight"]
    lines = ["# Warehouse Constellation", ""]
    if ds:
        lines += [f"Dataset **{ds}** feeds {len(view['highlighted_columns'])} columns across "
                  f"{len(view['highlighted_tables'])} tables (marked {HIGHLIGHT}).", ""]

    lines += ["```mermaid", "graph LR"]
    for t in view["tables"]:
        label = t["name"] + (f" {HIGHLIGHT}" if t["highlighted"] else "")
        shape = f'{t["name"]}(["{label}"])' if t["kind"] == "fact" else f'{t["name"]}["{label}"]'
        lines.append(f"    {shape}")
    for r in view["relationships"]:
        lines.append(f"    {r['from']} -->|{r['key']}| {r['to']}")
    if view["highlighted_tables"]:
        lines.append("    classDef hl stroke-width:3px,stroke:#d29922")
        lines.append(f"    class {','.join(view['highlighted_tables'])} hl")
    lines += ["```", ""]

    for t in view["tables"]:
        mark = f" {HIGHLIGHT}" if t["highlighted"] else ""
        role = "fact" if t["kind"] == "fact" else ("spine" if t["name"] in view["spines"] else "dimension")
        extra = f" — main fact of {', '.join(t['main_fact_of'])}" if t["main_fact_of"] else ""
        lines += [f"## {t['name']}{mark}", "",
                  f"{role}; used by {', '.join(t['schemas'])}{extra}", "",
                  "| | column | type | role | datasets |", "|---|---|---|---|---|"]
        for c in t["columns"]:
            name = f"**{c['name']}**" if c["highlighted"] else c["name"]
            lines.append(f"| {HIGHLIGHT if c['highlighted'] else ''} | {name} | {c['type']} | {c['role']} | "
                         f"{', '.join(c['datasets'])} |")
        lines.append("")
    return "\n".join(lines)
