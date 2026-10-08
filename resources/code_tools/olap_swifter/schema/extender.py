"""
Additive Schema Extension & Dataset Attachment.

`apply_patch` grows a registered schema in place. Patches may only add:
tables, columns, aliases, links, allowed codes and excluded variables. Nothing
existing is renamed, retyped or removed, so every dataset already loaded stays
valid. Each change is written to the schema's changelog.

`attach_dataset` records a dataset (its catalogue, EDA and description paths)
on a schema and tags every column the dataset feeds, so the schema file shows
which variables of the constellation belong to which dataset.

Patch format (YAML):

    base: hiv_cohort
    reason: "Add viral-load variables from site B export"
    add_tables:     { fact_x: {kind: fact, links: [...], columns: {...}} }
    add_columns:    { dim_patient: { art_start_date: {type: DATE, role: attribute} } }
    add_aliases:    { dim_patient.gender: [sex_at_birth] }
    add_links:      { fact_x: [dim_regimen] }
    add_allowed_values: { fact_x.outcome: ["LTFU"] }
    add_excluded_variables: { patient_name: "direct identifier, not warehoused" }
"""

import copy
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.schema.spec import (
    SchemaSpec, SchemaSpecError, attach_crosswalks, column_index, normalize_name, parse_spec,
)

PATCH_KEYS = {"base", "reason", "add_tables", "add_columns", "add_aliases", "add_links",
              "add_allowed_values", "add_excluded_variables"}


def _split_ref(ref: str) -> Tuple[str, str]:
    table, _, column = ref.partition(".")
    if not table or not column:
        raise SchemaSpecError(f"'{ref}' must be written as table.column")
    return table, column


def _reused_table(registry: SchemaRegistry, name: str, own_schema: str) -> Optional[Dict[str, Any]]:
    """YAML of an existing table from another schema, so a patch can reuse it by name."""
    for other in registry.all():
        if other.schema_id != own_schema and name in other.tables:
            tbl = copy.deepcopy(other.tables[name].to_yaml())
            for c in tbl.get("columns", {}).values():
                c.pop("datasets", None)
            return tbl
    return None


def apply_patch(spec: SchemaSpec, patch: Dict[str, Any],
                registry: Optional[SchemaRegistry] = None) -> SchemaSpec:
    unknown = set(patch) - PATCH_KEYS
    if unknown:
        raise SchemaSpecError(f"unknown patch keys {sorted(unknown)}; patches are additive only")
    if not patch.get("reason"):
        raise SchemaSpecError("a patch needs a 'reason' (it is written to the changelog)")

    data = copy.deepcopy(spec.to_yaml_dict())
    tables = data["tables"]
    changes: List[str] = []

    for name, t_raw in (patch.get("add_tables") or {}).items():
        if name in tables:
            raise SchemaSpecError(f"table '{name}' already exists; add columns to it instead")
        reused = _reused_table(registry, name, spec.schema_id) if registry else None
        if reused is not None:
            # Same name means same table: start from the existing definition, then add.
            extra_cols = (t_raw or {}).get("columns") or {}
            for cname in extra_cols:
                if cname in reused.get("columns", {}):
                    raise SchemaSpecError(f"{name}.{cname} already exists in the reused table")
            reused.setdefault("columns", {}).update(extra_cols)
            for l in (t_raw or {}).get("links") or []:
                if l not in reused.setdefault("links", []):
                    reused["links"].append(l)
            tables[name] = reused
            changes.append(f"reuse table {name}")
        else:
            tables[name] = t_raw
            changes.append(f"+table {name}")

    for tname, cols in (patch.get("add_columns") or {}).items():
        if tname not in tables:
            raise SchemaSpecError(f"add_columns: table '{tname}' does not exist")
        tcols = tables[tname].setdefault("columns", {})
        for cname, c_raw in cols.items():
            if cname in tcols:
                raise SchemaSpecError(f"column {tname}.{cname} already exists; existing columns cannot change")
            tcols[cname] = c_raw
            changes.append(f"+column {tname}.{cname}")

    for ref, aliases in (patch.get("add_aliases") or {}).items():
        tname, cname = _split_ref(ref)
        col = tables.get(tname, {}).get("columns", {}).get(cname)
        if col is None:
            raise SchemaSpecError(f"add_aliases: {ref} does not exist")
        existing = col.setdefault("aliases", [])
        existing.extend(a for a in aliases if a not in existing)
        changes.append(f"+aliases {ref}")

    for tname, links in (patch.get("add_links") or {}).items():
        if tname not in tables:
            raise SchemaSpecError(f"add_links: table '{tname}' does not exist")
        existing = tables[tname].setdefault("links", [])
        existing.extend(l for l in links if l not in existing)
        changes.append(f"+links {tname} -> {', '.join(links)}")

    for ref, codes in (patch.get("add_allowed_values") or {}).items():
        tname, cname = _split_ref(ref)
        col = tables.get(tname, {}).get("columns", {}).get(cname)
        if col is None:
            raise SchemaSpecError(f"add_allowed_values: {ref} does not exist")
        if not col.get("allowed"):
            raise SchemaSpecError(f"{ref} has no allowed list; adding one would restrict existing data")
        col["allowed"].extend(c for c in codes if c not in col["allowed"])
        changes.append(f"+codes {ref}")

    for var, why in (patch.get("add_excluded_variables") or {}).items():
        data.setdefault("excluded_variables", {})[var] = why
        changes.append(f"+excluded {var}")

    if not changes:
        raise SchemaSpecError("patch adds nothing")

    data.setdefault("changelog", []).append(
        f"{date.today().isoformat()}: {patch['reason']} ({'; '.join(changes)})"
    )
    patched = parse_spec(data)
    # Crosswalk files sit beside the schema files; reload them for the patched spec.
    return attach_crosswalks(patched, (registry or SchemaRegistry()).dir)


def attach_dataset(spec: SchemaSpec, dataset: str, variables: List[Dict[str, Any]],
                   catalogue: str, eda: str, description: Optional[str] = None,
                   main_fact: Optional[str] = None) -> Dict[str, Any]:
    """
    Records the dataset on the schema and tags every column its variables feed.
    Returns the columns tagged, for highlighting.
    """
    ds = normalize_name(dataset)
    main_fact = main_fact or spec.main_fact
    if main_fact not in spec.tables or not spec.tables[main_fact].is_fact:
        raise SchemaSpecError(f"main fact '{main_fact}' is not a fact table of {spec.schema_id}")
    present = {normalize_name(v["name"]) for v in variables}
    idx = column_index(spec)
    tagged = set()
    for n in present:
        if n in idx:
            tagged.add(idx[n])
    for c in spec.all_columns():
        if c.expr and all(normalize_name(i) in present for i in c.inputs):
            tagged.add((c.table, c.name))
    by_name = {c.name: c for c in spec.all_columns()}
    for c in spec.all_columns():  # a crosswalk column is fed by whoever feeds its raw label
        if c.crosswalk and c.crosswalk["from"] in by_name:
            src = by_name[c.crosswalk["from"]]
            if (src.table, src.name) in tagged:
                tagged.add((c.table, c.name))
    for table, cname in tagged:
        col = spec.tables[table].columns[cname]
        if ds not in col.datasets:
            col.datasets.append(ds)
    entry = {"catalogue": str(catalogue), "eda": str(eda), "main_fact": main_fact,
             "attached": date.today().isoformat(),
             "variables": len(variables), "columns_fed": len(tagged)}
    if description:
        entry["description"] = str(description)
    spec.datasets[ds] = entry
    return {"dataset": ds, "columns": sorted(f"{t}.{c}" for t, c in tagged)}


def extend_schema(patch_path: str, registry: Optional[SchemaRegistry] = None) -> SchemaSpec:
    """Applies a patch file to its base schema and writes the schema file in place."""
    registry = registry or SchemaRegistry()
    patch = yaml.safe_load(Path(patch_path).read_text(encoding="utf-8")) or {}
    if "base" not in patch:
        raise SchemaSpecError("patch needs 'base' (the schema to extend)")
    new_spec = apply_patch(registry.get(str(patch["base"])), patch, registry)
    conflicts = registry.table_conflicts(new_spec)
    if conflicts:
        raise SchemaSpecError("; ".join(conflicts))
    return new_spec
