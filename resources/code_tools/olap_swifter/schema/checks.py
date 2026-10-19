"""
Schema-Driven Checks.
Every check is generated from the schema spec, so any dataset (AMR, HIV, ...)
gets the same guarantees without dataset-specific code. Checks return counts
and pass/fail only — never row values — which is what the orchestrating skill
is allowed to see when real data is loaded.

The goal is a full transfer of the source, not cleaning it. So checks have
three severities:

  error       the transfer is broken: a value would be lost or changed, a key
              would not join. Blocks the load (unless forced).
  data_issue  the source itself has a problem (a value outside its range, a
              code outside the vocabulary, a required value missing, an entity
              recorded two ways). Never blocks: the value is transferred as it
              is, and the issue is reported for cleaning (workspace README).
  info        context only.

Preflight (on the staged source, before anything is written):
  errors:      variable_coverage, spine_key_present, spine_key_nulls,
               cast_failures, derived_completeness, crosswalk_coverage, fact_grain
  data issues: allowed_values, value_range, required_nulls, entity_consistency
  info:        keys_shared_with_other_sources

crosswalk_coverage is the one check that names values: the raw labels a
crosswalk does not map yet (vocabulary such as ward names, never identifiers),
so the skill can extend the crosswalk.

Post-load (staged source vs warehouse, for this source only):
  errors: fact_row_parity, measure_sum_parity, value_parity (every value of
          every table equals the source's), derived_parity (each derived
          column recomputed from the source's raw columns), entity_coverage,
          orphan_keys, lineage
  info:   spine_coverage
"""

from typing import Any, Dict, List, Optional

import duckdb

from olap_swifter.schema.materializer import (
    DATA_SOURCE_TABLE, KEYMAP, Resolution, attach_staging, content_columns, crosswalk_raw_sql, fact_row_filter,
    projection_sql, q, use_surrogates,
)
from olap_swifter.schema.spec import SchemaSpec

SUM_TOLERANCE = 1e-6  # relative
MAX_UNMAPPED_LABELS = 50


def _result(check: str, target: str, passed: bool, expected: Any = None, actual: Any = None,
            detail: str = "", phase: str = "preflight", severity: str = "error") -> Dict[str, Any]:
    return {"check": check, "target": target, "passed": bool(passed), "expected": expected,
            "actual": actual, "detail": detail, "phase": phase, "severity": severity}


def _scalar(conn, sql: str, params: Optional[list] = None):
    return conn.execute(sql, params or []).fetchone()[0]


def _sql_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def run_preflight(conn: duckdb.DuckDBPyConnection, spec: SchemaSpec, res: Resolution) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    stg = res.ref

    out.append(_result(
        "variable_coverage", "source", not res.unmapped, 0, len(res.unmapped),
        "unmapped variables: " + ", ".join(res.unmapped) if res.unmapped
        else "every variable is mapped or explicitly excluded",
    ))

    spine_col = spec.key_column(spec.spine_table)
    spine_sql = res.sql(spine_col)
    out.append(_result("spine_key_present", f"{spec.spine_table}.{spec.spine_key}", spine_sql is not None,
                       detail="" if spine_sql else "no source variable resolves to the spine key"))
    if spine_sql:
        nulls = _scalar(conn, f"SELECT COUNT(*) FROM {stg} WHERE {spine_sql} IS NULL")
        out.append(_result("spine_key_nulls", f"{spec.spine_table}.{spec.spine_key}", nulls == 0, 0, nulls,
                           "rows without a visit key cannot join the spine" if nulls else ""))

    for col in spec.all_columns():
        col_sql, raw_sql = res.sql(col), res.raw_sql.get((col.table, col.name))
        if col_sql is None:
            continue
        target = f"{col.table}.{col.name}"
        if col.type != "VARCHAR":
            fails = _scalar(conn, f"""SELECT COUNT(*) FROM {stg}
                WHERE NULLIF(TRIM(CAST({raw_sql} AS VARCHAR)), '') IS NOT NULL AND {col_sql} IS NULL""")
            out.append(_result("cast_failures", target, fails == 0, 0, fails,
                               f"{fails} values could not be read as {col.type} and would be lost in transfer; "
                               "type the column to hold them (e.g. VARCHAR) or derive it explicitly" if fails else ""))
        if col.expr and col.role != "key":
            inputs = [raw for raw in (res.input_columns.get((col.table, col.name)) or [])]
            if inputs:
                present = " AND ".join(f"NULLIF(TRIM(CAST({q(i)} AS VARCHAR)), '') IS NOT NULL" for i in inputs)
                lost = _scalar(conn, f"SELECT COUNT(*) FROM {stg} WHERE {present} AND {col_sql} IS NULL")
                out.append(_result("derived_completeness", target, lost == 0, 0, lost,
                                   f"{lost} rows have every input ({', '.join(inputs)}) but no derived value: "
                                   "the expression cannot read them (e.g. an unparseable date)" if lost else ""))
        if col.allowed:
            allowed = ", ".join(_sql_literal(a) for a in col.allowed)
            bad = _scalar(conn, f"SELECT COUNT(*) FROM {stg} WHERE {col_sql} IS NOT NULL "
                                f"AND {col_sql} NOT IN ({allowed})")
            out.append(_result("allowed_values", target, bad == 0, 0, bad,
                               f"{bad} rows hold values outside the allowed codes; transferred as recorded"
                               if bad else "", severity="data_issue"))
        if col.range:
            lo, hi = col.range
            cond = []
            if lo is not None:
                cond.append(f"{col_sql} < CAST({_sql_literal(lo)} AS {col.type})")
            if hi is not None:
                cond.append(f"{col_sql} > CAST({_sql_literal(hi)} AS {col.type})")
            bad = _scalar(conn, f"SELECT COUNT(*) FROM {stg} WHERE {' OR '.join(cond)}")
            out.append(_result("value_range", target, bad == 0, 0, bad,
                               f"{bad} rows hold values outside {col.range}; transferred as recorded" if bad else "",
                               severity="data_issue"))
        if col.required and col.role != "key":
            nulls = _scalar(conn, f"SELECT COUNT(*) FROM {stg} WHERE {col_sql} IS NULL")
            out.append(_result("required_nulls", target, nulls == 0, 0, nulls,
                               f"{nulls} rows have no value for a required variable" if nulls else "",
                               severity="data_issue"))

    # Every raw label of a crosswalk column must map to a standard value.
    by_name = {c.name: c for c in spec.all_columns()}
    for col in spec.all_columns():
        if not col.crosswalk:
            continue
        label = crosswalk_raw_sql(spec, col, res, "from")
        if label is None:
            continue
        mapped = res.sql(col)
        clean = f"NULLIF(TRIM(CAST({label} AS VARCHAR)), '')"
        scope = crosswalk_raw_sql(spec, col, res, "scope")
        scope_sel = f", NULLIF(TRIM(CAST({scope} AS VARCHAR)), '')" if scope else ", NULL"
        rows = conn.execute(f"""SELECT {clean}{scope_sel}, COUNT(*) FROM {stg}
            WHERE {clean} IS NOT NULL AND {mapped} IS NULL GROUP BY ALL ORDER BY 3 DESC, 1""").fetchall()
        n_rows = sum(r[2] for r in rows)
        labels = sorted({r[0] for r in rows})
        source_col = by_name.get(col.crosswalk["from"])
        result = _result(
            "crosswalk_coverage", f"{col.table}.{col.name}", not rows, 0, n_rows,
            f"{len(labels)} raw label(s) of {col.crosswalk['from']} not mapped in "
            f"crosswalks/{col.crosswalk['file']}.yaml" if rows else "every raw label maps to a standard value")
        if rows and not (source_col and source_col.pii):
            result["unmapped"] = [{"value": r[0], "scope": r[1], "rows": r[2]} for r in rows[:MAX_UNMAPPED_LABELS]]
        out.append(result)

    # One entity, one description: a key must not carry conflicting attribute values.
    for dim in spec.dimensions:
        key_col = dim.columns[dim.key]
        if not res.resolved(key_col):
            continue
        attrs = [c for c in spec.table_columns(dim) if c.name != dim.key and res.resolved(c)]
        if not attrs:
            continue
        conflicts = " OR ".join(f"COUNT(DISTINCT {q(c.name)}) > 1" for c in attrs)
        n = _scalar(conn, f"""SELECT COUNT(*) FROM (
            SELECT {q(dim.key)} FROM ({projection_sql(spec, dim, res)}) src
            WHERE {q(dim.key)} IS NOT NULL GROUP BY {q(dim.key)} HAVING {conflicts})""")
        per_attr = []
        if n:
            for c in attrs:
                k = _scalar(conn, f"""SELECT COUNT(*) FROM (
                    SELECT {q(dim.key)} FROM ({projection_sql(spec, dim, res)}) src
                    WHERE {q(dim.key)} IS NOT NULL GROUP BY {q(dim.key)}
                    HAVING COUNT(DISTINCT {q(c.name)}) > 1)""")
                if k:
                    per_attr.append(f"{c.name}: {k}")
        out.append(_result(
            "entity_consistency", dim.name, n == 0, 0, n,
            "entities recorded with conflicting values (attribute: entities) — " + ", ".join(per_attr) +
            "; the first recorded value is kept on the dimension. If the attribute really changes between "
            "encounters, it belongs on the visit or a fact" if n else "", severity="data_issue",
        ))

    # Keys this source shares with sources already loaded. Expected when the same
    # visits arrive from several exports; a surprise when two sites reuse ids.
    sid = res.staging[len("stg_"):]
    for dim in spec.dimensions:
        nat = res.natural_sql.get((dim.name, dim.key))
        if nat is None:
            continue
        shared = _scalar(conn, f"""SELECT COUNT(DISTINCT {nat}) FROM {stg}
            WHERE {nat} IN (SELECT natural_key FROM {KEYMAP}
                            WHERE dimension = ? AND source_id IS DISTINCT FROM ?)""", [dim.name, sid])
        if shared:
            out.append(_result("keys_shared_with_other_sources", f"{dim.name}.{dim.key}", True, None, shared,
                               f"{shared} keys already exist from other sources and will be merged; "
                               "confirm they identify the same entities", severity="info"))

    # A declared grain must identify exactly one fact row.
    for fact in spec.facts:
        where = fact_row_filter(spec, fact, res)
        if where is None or not fact.grain:
            continue
        cols = ", ".join(q(c.name) for c in spec.table_columns(fact))
        grain = ", ".join(q(g) for g in fact.grain)
        dupes = _scalar(conn, f"""SELECT COUNT(*) FROM (
            SELECT {grain} FROM (SELECT DISTINCT {cols} FROM ({projection_sql(spec, fact, res)}) src WHERE {where})
            GROUP BY {grain} HAVING COUNT(*) > 1)""")
        out.append(_result("fact_grain", fact.name, dupes == 0, 0, dupes,
                           f"{dupes} grain values ({', '.join(fact.grain)}) have more than one row" if dupes else ""))
    return out


def run_postload(conn: duckdb.DuckDBPyConnection, spec: SchemaSpec, res: Resolution,
                 source_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}

    for fact in spec.facts:
        where = fact_row_filter(spec, fact, res)
        if where is None or fact.name not in tables:
            continue
        proj = projection_sql(spec, fact, res)
        cols = [c.name for c in spec.table_columns(fact)]
        col_list = ", ".join(q(c) for c in cols)
        expected = _scalar(conn, f"SELECT COUNT(*) FROM (SELECT DISTINCT {col_list} FROM ({proj}) src WHERE {where})")
        actual = _scalar(conn, f"SELECT COUNT(*) FROM {q(fact.name)} WHERE _source_id = ?", [source_id])
        out.append(_result("fact_row_parity", fact.name, expected == actual, expected, actual, phase="postload"))

        for m in (c for c in fact.columns.values() if c.role == "measure" and res.resolved(c)):
            exp = _scalar(conn, f"""SELECT SUM({q(m.name)}) FROM
                (SELECT DISTINCT {col_list} FROM ({proj}) src WHERE {where})""")
            act = _scalar(conn, f"SELECT SUM({q(m.name)}) FROM {q(fact.name)} WHERE _source_id = ?", [source_id])
            raw_total = _scalar(conn, f"SELECT SUM({res.sql(m)}) FROM {res.ref}")
            exp_f, act_f = float(exp or 0), float(act or 0)
            ok = abs(exp_f - act_f) <= SUM_TOLERANCE * max(1.0, abs(exp_f))
            fan_out = "" if raw_total is None or abs(float(raw_total) - exp_f) < 1e-9 else \
                f"raw rows repeat this measure (raw sum {float(raw_total):.4f}); parity is on de-duplicated fact rows"
            out.append(_result("measure_sum_parity", f"{fact.name}.{m.name}", ok, exp_f, act_f,
                               fan_out, phase="postload"))

    for dim in spec.dimensions:
        key_col = dim.columns[dim.key]
        if not res.resolved(key_col) or dim.name not in tables:
            continue
        k = q(dim.key)
        expected = _scalar(conn, f"SELECT COUNT(DISTINCT {res.sql(key_col)}) FROM {res.ref}")
        actual = _scalar(conn, f"""SELECT COUNT(*) FROM {q(dim.name)} d WHERE d.{k} IN
            (SELECT {res.sql(key_col)} FROM {res.ref})""")
        out.append(_result("entity_coverage", dim.name, expected == actual, expected, actual,
                           "" if expected == actual else "some source entities are missing from the dimension",
                           phase="postload"))

    for rel in spec.relationships():
        src_t, dst_t, key = rel["from"], rel["to"], rel["key"]
        if src_t not in tables or dst_t not in tables:
            continue
        src_spec = spec.tables[src_t]
        fed = fact_row_filter(spec, src_spec, res) is not None if src_spec.is_fact \
            else res.resolved(src_spec.columns[src_spec.key])
        if not fed:
            continue  # this source wrote nothing to that table
        scope = "f._source_id = ?" if spec.tables[src_t].is_fact else \
            f"f.{q(key)} IN (SELECT {q(key)} FROM {q(src_t)})"
        params = [source_id] if spec.tables[src_t].is_fact else []
        orphans = _scalar(conn, f"""SELECT COUNT(*) FROM {q(src_t)} f
            WHERE {scope} AND f.{q(key)} IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM {q(dst_t)} d WHERE d.{q(key)} = f.{q(key)})""", params)
        out.append(_result("orphan_keys", f"{src_t}.{key} -> {dst_t}", orphans == 0, 0, orphans,
                           phase="postload"))

    out.extend(_value_parity(conn, spec, res, source_id, tables))

    registered = _scalar(conn, f"SELECT COUNT(*) FROM {DATA_SOURCE_TABLE} WHERE source_id = ?", [source_id])
    out.append(_result("lineage", DATA_SOURCE_TABLE, registered == 1, 1, registered, phase="postload"))

    # The dataset's own main fact when the source is an attached dataset, else the schema's.
    main_name = spec.dataset_for_source(source_id).get("main_fact") or spec.main_fact
    main = spec.tables.get(main_name)
    if main is not None and main.name in tables and spec.spine_table in tables:
        visits = _scalar(conn, f"SELECT COUNT(DISTINCT {q(spec.spine_key)}) FROM {q(main.name)} WHERE _source_id = ?",
                         [source_id])
        spine_sql = res.sql(spec.key_column(spec.spine_table))
        total = _scalar(conn, f"SELECT COUNT(DISTINCT {spine_sql}) FROM {res.ref}") if spine_sql else 0
        pct = round(100.0 * visits / total, 2) if total else 0.0
        out.append(_result("spine_coverage", f"{main_name} over {spec.spine_table}", True, None, pct,
                           f"{pct}% of the source's visits have a row in the main fact",
                           phase="postload", severity="info"))
    return out


def _expected_rows(spec: SchemaSpec, table, res: Resolution, source_id: str, cols: List[str]) -> Optional[str]:
    """The rows the source should have put in `table` (restricted to `cols`), from the staged source."""
    col_list = ", ".join(q(c) for c in cols)
    proj = projection_sql(spec, table, res)
    if table.is_fact:
        where = fact_row_filter(spec, table, res)
        return None if where is None else f"SELECT DISTINCT {col_list} FROM ({proj}) src WHERE {where}"
    k = q(table.key)
    attrs = [c for c in cols if c != table.key]
    agg = "".join(f", arg_min({q(c)}, _source_row) FILTER (WHERE {q(c)} IS NOT NULL) AS {q(c)}" for c in attrs)
    return (f"SELECT {k}{agg} FROM ({proj}) src WHERE {k} IS NOT NULL "
            f"AND {k} IN (SELECT {k} FROM {q(table.name)} WHERE _source_id = {_sql_literal(source_id)}) GROUP BY {k}")


def _mismatch(conn, expected: str, actual: str) -> int:
    return _scalar(conn, f"SELECT (SELECT COUNT(*) FROM (({expected}) EXCEPT ALL ({actual}))) + "
                         f"(SELECT COUNT(*) FROM (({actual}) EXCEPT ALL ({expected})))")


def _value_parity(conn, spec: SchemaSpec, res: Resolution, source_id: str, tables) -> List[Dict[str, Any]]:
    """
    Full transfer: every row this source wrote equals, value for value, what the
    staged source holds (cast and derived columns included). Then each derived
    column (expression or crosswalk) on its own, so a failure names it.
    """
    out = []
    sid = _sql_literal(source_id)
    for table in spec.tables.values():
        if table.name not in tables:
            continue
        cols = [c.name for c in spec.table_columns(table) if res.resolved(c)]
        if not table.is_fact and table.key not in cols:
            continue
        if not cols:
            continue
        expected = _expected_rows(spec, table, res, source_id, cols)
        if expected is None:
            continue
        actual = f"SELECT {', '.join(q(c) for c in cols)} FROM {q(table.name)} WHERE _source_id = {sid}"
        bad = _mismatch(conn, expected, actual)
        out.append(_result("value_parity", table.name, bad == 0, 0, bad,
                           f"{bad} rows differ between the source and the warehouse" if bad else
                           f"{len(cols)} columns transferred value for value", phase="postload"))
        anchor = [table.key] if not table.is_fact else [g for g in table.grain if g in cols]
        for c in spec.table_columns(table):
            if not (c.expr or c.crosswalk) or c.role == "key" or c.name not in cols:
                continue
            these = anchor + [c.name]
            exp = _expected_rows(spec, table, res, source_id, these)
            act = f"SELECT {', '.join(q(x) for x in these)} FROM {q(table.name)} WHERE _source_id = {sid}"
            if table.is_fact:
                exp = f"SELECT DISTINCT * FROM ({exp})"
                act = f"SELECT DISTINCT * FROM ({act})"
            bad = _mismatch(conn, exp, act)
            out.append(_result("derived_parity", f"{table.name}.{c.name}", bad == 0, 0, bad,
                               f"{bad} derived values differ from the derivation on the source's raw columns"
                               if bad else "", phase="postload"))
    return out


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pass/fail counts over errors only; data issues are listed apart and never fail."""
    errors = [r for r in results if r["severity"] == "error"]
    failed = [r for r in errors if not r["passed"]]
    issues = [r for r in results if r["severity"] == "data_issue" and not r["passed"]]
    return {
        "total": len(errors),
        "passed": len(errors) - len(failed),
        "failed": len(failed),
        "all_passed": not failed,
        "failures": failed,
        "data_issues": issues,
        "results": results,
    }


def run_checks(conn: duckdb.DuckDBPyConnection, spec: SchemaSpec, source_id: str) -> Dict[str, Any]:
    """Re-runs preflight and post-load checks for an already loaded source."""
    from olap_swifter.schema.materializer import resolve_source
    attach_staging(conn)
    res = resolve_source(spec, conn, f"stg_{source_id}")
    preflight = summarize(run_preflight(conn, spec, res))  # on the source's own identifiers
    use_surrogates(spec, res)                                # post-load compares generated keys
    return {
        "source_id": source_id,
        "schema": spec.ref,
        "preflight": preflight,
        "postload": summarize(run_postload(conn, spec, res, source_id)),
    }
