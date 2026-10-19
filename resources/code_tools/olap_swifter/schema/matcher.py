"""
Schema Matcher.
Scores a new source (its variable list from a profile and/or data dictionary)
against every registered schema and recommends one of:

  fit         every variable already has a home; load as-is
  extend      overlap is good enough; add the unmatched variables with an
              additive patch (new columns, aliases, tables, links)
  new_schema  the source is too different (>= `new_schema_min_difference`
              of its variables unmatched); model a new schema

Blockers (unresolved spine key, type conflicts, missing required columns)
are reported alongside the decision; they must be fixed by the patch.
"""

from typing import Any, Dict, List, Optional

from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.schema.spec import (
    SchemaSpec, column_index, normalize_name, normalize_type, types_compatible,
)


def _resolvable(col, present: set, spec: Optional[SchemaSpec] = None) -> bool:
    if col.crosswalk:
        src = next((c for c in spec.all_columns() if c.name == col.crosswalk["from"]), None) if spec else None
        return bool(src) and _resolvable(src, present, spec)
    if col.from_key:  # the key's own source value: resolvable whenever the key is
        return _resolvable(spec.tables[col.table].columns[col.from_key], present, spec) if spec else False
    if col.expr:
        return all(normalize_name(i) in present for i in col.inputs)
    return any(n in present for n in col.match_names())


def match_schema(variables: List[Dict[str, Any]], spec: SchemaSpec,
                 new_schema_min_difference: float = 0.8) -> Dict[str, Any]:
    idx = column_index(spec)
    expr_inputs: Dict[str, str] = {}
    for c in spec.all_columns():
        if c.expr:
            for i in c.inputs:
                expr_inputs[normalize_name(i)] = f"{c.table}.{c.name}"
    excluded = {normalize_name(k): v for k, v in spec.excluded_variables.items()}

    matched, unmatched, excluded_hits, conflicts, code_gaps = {}, [], {}, [], []
    present = {normalize_name(v["name"]) for v in variables}

    for v in variables:
        n = normalize_name(v["name"])
        src_type = v.get("type") if v.get("type") else normalize_type(v.get("declared_type"))
        if n in excluded:
            excluded_hits[v["name"]] = excluded[n]
        elif n in idx:
            table, cname = idx[n]
            col = spec.tables[table].columns[cname]
            matched[v["name"]] = f"{table}.{cname}"
            if not types_compatible(col.type, src_type):
                conflicts.append({"variable": v["name"], "source_type": src_type,
                                  "target": f"{table}.{cname}", "target_type": col.type})
            if col.allowed and v.get("allowed_values"):
                allowed = {str(a) for a in col.allowed}
                extra = [a for a in v["allowed_values"] if str(a) not in allowed]
                if extra:
                    # Codes come from the dictionary (metadata), never from the data.
                    code_gaps.append({"variable": v["name"], "target": f"{table}.{cname}",
                                      "codes_not_in_schema": extra})
        elif n in expr_inputs:
            matched[v["name"]] = f"{expr_inputs[n]} (expr input)"
        else:
            unmatched.append(v["name"])

    total = len(variables)
    known = len(matched) + len(excluded_hits)
    difference = round(1 - known / total, 4) if total else 1.0

    spine_col = spec.key_column(spec.spine_table)
    spine_resolved = _resolvable(spine_col, present, spec)
    # Required columns only bind tables this dataset feeds; the spine is always fed.
    tables_touched = {m.split(" ")[0].split(".")[0] for m in matched.values()} | {spec.spine_table}
    missing_required = [
        f"{c.table}.{c.name}" for c in spec.all_columns()
        if c.required and c.table in tables_touched and not _resolvable(c, present, spec)
    ]
    # Every dimension a loaded table links to needs its key, or rows cannot be joined.
    missing_link_keys = sorted({
        f"{link}.{spec.tables[link].key}"
        for t in tables_touched for link in spec.table_links(spec.tables[t])
        if not _resolvable(spec.key_column(link), present, spec)
    })

    blockers = []
    if not spine_resolved:
        blockers.append(f"spine key {spec.spine_table}.{spec.spine_key} has no source variable; "
                        "add an alias or an expr that derives it")
    blockers += [f"type conflict: {c['variable']} ({c['source_type']}) -> {c['target']} ({c['target_type']})"
                 for c in conflicts]
    blockers += [f"required column {m} has no source variable" for m in missing_required]
    blockers += [f"linked dimension key {m} has no source variable" for m in missing_link_keys]

    if difference >= new_schema_min_difference:
        decision = "new_schema"
    elif unmatched or blockers or code_gaps:
        decision = "extend"
    else:
        decision = "fit"

    return {
        "schema": spec.ref,
        "title": spec.title,
        "decision": decision,
        "difference": difference,
        "overlap": round(1 - difference, 4),
        "threshold": new_schema_min_difference,
        "variables_total": total,
        "matched": matched,
        "excluded": excluded_hits,
        "unmatched": unmatched,
        "spine_resolved": spine_resolved,
        "type_conflicts": conflicts,
        "code_gaps": code_gaps,
        "missing_required": missing_required,
        "missing_link_keys": missing_link_keys,
        "blockers": blockers,
    }


def match_registry(variables: List[Dict[str, Any]],
                   registry: Optional[SchemaRegistry] = None) -> Dict[str, Any]:
    """Ranks every registered schema; the best one carries the recommendation."""
    registry = registry or SchemaRegistry()
    threshold = float(registry.config()["new_schema_min_difference"])
    specs = registry.all()
    results = [match_schema(variables, s, threshold) for s in specs]
    results.sort(key=lambda r: (r["difference"], len(r["blockers"])))
    best = results[0] if results else None

    # Unmatched variables that another schema already models: reuse those tables, don't redefine.
    if best:
        others = [s for s in specs if s.schema_id != best["schema"]]
        reuse = {}
        for v in best["unmatched"]:
            n = normalize_name(v)
            for s in others:
                hit = column_index(s).get(n)
                if hit:
                    reuse[v] = f"{s.schema_id}: {hit[0]}.{hit[1]}"
                    break
        best["reusable_from_other_schemas"] = reuse

    tied = [r["schema"] for r in results[1:]
            if best and (r["difference"], len(r["blockers"])) == (best["difference"], len(best["blockers"]))]
    return {
        "variables_total": len(variables),
        "threshold": threshold,
        "recommendation": best["decision"] if best else "new_schema",
        "best_schema": best["schema"] if best else None,
        # Equal scores: the description (study, related datasets) must decide, not list order.
        "tied_with": tied,
        "candidates": results,
    }
