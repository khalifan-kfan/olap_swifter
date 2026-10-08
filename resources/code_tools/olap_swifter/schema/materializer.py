"""
Schema Materializer & Loader (ETL).
Turns a registered schema into DuckDB tables and loads raw sources into them.

All schemas share one warehouse, so the model grows into a single fact
constellation: shared dimensions (visit spine, patient, facility, ...) are
created once and gain columns as schemas extend them, while each schema adds
its own facts. Every row carries `_source_id`, and `dim_data_source` records
which file, schema (and the schema file's hash) and load produced it.

Loading is metadata-driven and never returns row values: callers get counts.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

from olap_swifter.schema.spec import ColumnSpec, SchemaSpec, TableSpec, label_sql, normalize_label, normalize_name

DATA_SOURCE_TABLE = "dim_data_source"


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def source_id_for(name: str) -> str:
    sid = normalize_name(name)
    if not sid:
        raise ValueError("source name must contain letters or digits")
    return sid


@dataclass
class Resolution:
    """How each schema column is computed from one staged source."""
    staging: str
    raw_columns: List[str]
    column_sql: Dict[Tuple[str, str], Optional[str]] = field(default_factory=dict)
    raw_sql: Dict[Tuple[str, str], Optional[str]] = field(default_factory=dict)
    used_raw: set = field(default_factory=set)
    excluded_raw: Dict[str, str] = field(default_factory=dict)

    @property
    def unmapped(self) -> List[str]:
        return [c for c in self.raw_columns
                if c not in self.used_raw and c not in self.excluded_raw and c != "_source_row"]

    def sql(self, col: ColumnSpec) -> Optional[str]:
        return self.column_sql.get((col.table, col.name))

    def resolved(self, col: ColumnSpec) -> bool:
        return self.sql(col) is not None


def cast_sql(raw_sql: str, col_type: str) -> str:
    if col_type == "VARCHAR":
        return f"NULLIF(TRIM(CAST({raw_sql} AS VARCHAR)), '')"
    return f"TRY_CAST(NULLIF(TRIM(CAST({raw_sql} AS VARCHAR)), '') AS {col_type})"


def resolve_source(spec: SchemaSpec, conn: duckdb.DuckDBPyConnection, staging: str) -> Resolution:
    raw_cols = [r[0] for r in conn.execute(f"DESCRIBE {q(staging)}").fetchall()]
    by_norm = {normalize_name(c): c for c in raw_cols if c != "_source_row"}
    res = Resolution(staging=staging, raw_columns=raw_cols)
    excluded = {normalize_name(k): v for k, v in spec.excluded_variables.items()}
    for norm, raw in by_norm.items():
        if norm in excluded:
            res.excluded_raw[raw] = excluded[norm]

    for col in spec.all_columns():
        key = (col.table, col.name)
        if col.crosswalk:
            continue  # second pass, once its raw-label column is resolved
        if col.expr:
            inputs = [by_norm.get(normalize_name(i)) for i in col.inputs]
            if all(inputs):
                res.raw_sql[key] = f"({col.expr})"
                res.column_sql[key] = cast_sql(f"({col.expr})", col.type)
                res.used_raw.update(inputs)
            else:
                res.raw_sql[key] = res.column_sql[key] = None
            continue
        raw = next((by_norm[n] for n in col.match_names() if n in by_norm), None)
        res.raw_sql[key] = q(raw) if raw else None
        res.column_sql[key] = cast_sql(q(raw), col.type) if raw else None
        if raw:
            res.used_raw.add(raw)

    for col in spec.all_columns():
        if col.crosswalk:
            res.raw_sql[(col.table, col.name)] = sql = crosswalk_sql(spec, col, res)
            res.column_sql[(col.table, col.name)] = sql
    return res


def _lit(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def crosswalk_raw_sql(spec: SchemaSpec, col: ColumnSpec, res: Resolution, ref: str = "from") -> Optional[str]:
    """Raw SQL of the column a crosswalk reads its label (`from`) or scope (`scope`) from."""
    name = col.crosswalk.get(ref)
    if not name:
        return None
    return next((res.raw_sql.get((c.table, c.name)) for c in spec.all_columns()
                 if c.name == name and res.raw_sql.get((c.table, c.name))), None)


def crosswalk_sql(spec: SchemaSpec, col: ColumnSpec, res: Resolution) -> Optional[str]:
    """
    CASE expression mapping the raw label to its standard value. Labels compare
    case- and whitespace-insensitively; scoped entries (one site's own wording)
    win over general ones. Unmapped labels give NULL, which the
    crosswalk_coverage gate counts.
    """
    label = crosswalk_raw_sql(spec, col, res, "from")
    if label is None:
        return None
    xw = spec.crosswalks.get(col.crosswalk["file"]) or {"entries": []}
    scope = crosswalk_raw_sql(spec, col, res, "scope")
    lbl = label_sql(label)
    whens = []
    for e in sorted(xw["entries"], key=lambda e: e["scope"] is None):
        if not e["maps_to"]:
            continue
        cond = f"{lbl} = {_lit(normalize_label(e['value']))}"
        if e["scope"]:
            if scope is None:
                continue
            cond += f" AND {label_sql(scope)} = {_lit(normalize_label(e['scope']))}"
        whens.append(f"WHEN {cond} THEN {_lit(e['maps_to'])}")
    if not whens:
        return "CAST(NULL AS VARCHAR)"
    return f"(CASE {' '.join(whens)} END)"


def content_columns(spec: SchemaSpec, table: TableSpec) -> List[ColumnSpec]:
    """Columns whose presence makes a fact row exist: own columns and non-spine link keys."""
    links = [spec.key_column(l) for l in spec.table_links(table) if l != spec.spine_table]
    return list(table.columns.values()) + links


def projection_sql(spec: SchemaSpec, table: TableSpec, res: Resolution) -> str:
    """`SELECT <cast columns>, _source_row FROM staging` for one table."""
    parts = []
    for col in spec.table_columns(table):
        expr = res.sql(col)
        parts.append(f"{expr if expr else f'CAST(NULL AS {col.type})'} AS {q(col.name)}")
    parts.append("_source_row")
    return f"SELECT {', '.join(parts)} FROM {q(res.staging)}"


def fact_row_filter(spec: SchemaSpec, table: TableSpec, res: Resolution) -> Optional[str]:
    """WHERE clause selecting staged rows that hold a fact; None if the source carries none."""
    content = [c for c in content_columns(spec, table) if res.resolved(c)]
    if not content:
        return None
    any_content = " OR ".join(f"{q(c.name)} IS NOT NULL" for c in content)
    return f"{q(spec.spine_key)} IS NOT NULL AND ({any_content})"


class WarehouseLoader:
    """Creates schema tables in a DuckDB warehouse and loads sources into them."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn
        self._ensure_data_source_table()

    def _ensure_data_source_table(self):
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {DATA_SOURCE_TABLE} (
                source_id VARCHAR,
                source_name VARCHAR,
                file_name VARCHAR,
                file_sha256 VARCHAR,
                schema_id VARCHAR,
                schema_sha256 VARCHAR,
                is_synthetic BOOLEAN,
                row_count BIGINT,
                column_count BIGINT,
                loaded_at TIMESTAMP
            )
        """)

    # ---------- DDL ----------

    def _existing_columns(self, table: str) -> Dict[str, str]:
        rows = self.conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [table]
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def ensure_tables(self, spec: SchemaSpec) -> Dict[str, List[str]]:
        """Creates missing tables; adds missing columns to existing ones. Returns what changed."""
        changed: Dict[str, List[str]] = {}
        for table in spec.tables.values():
            wanted = [(c.name, c.type) for c in spec.table_columns(table)]
            wanted.append(("_source_id", "VARCHAR"))
            if table.is_fact:
                wanted.append(("_source_row", "BIGINT"))
            existing = self._existing_columns(table.name)
            if not existing:
                cols = ", ".join(f"{q(n)} {t}" for n, t in wanted)
                self.conn.execute(f"CREATE TABLE {q(table.name)} ({cols})")
                changed[table.name] = ["created"]
                continue
            for name, col_type in wanted:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {q(table.name)} ADD COLUMN {q(name)} {col_type}")
                    changed.setdefault(table.name, []).append(f"+{name}")
                elif existing[name].upper() != col_type:
                    raise TypeError(f"{table.name}.{name} is {existing[name]} in the warehouse "
                                    f"but {col_type} in {spec.ref}")
        return changed

    # ---------- staging ----------

    def stage_csv(self, path: str, source_id: str) -> str:
        staging = f"stg_{source_id}"
        self.conn.execute(f"""
            CREATE OR REPLACE TABLE {q(staging)} AS
            SELECT *, CAST(row_number() OVER () AS BIGINT) AS _source_row
            FROM read_csv({_sql_str(path)}, all_varchar = true, header = true)
        """)
        return staging

    # ---------- loading ----------

    def _load_dimension(self, spec: SchemaSpec, table: TableSpec, res: Resolution, sid: str) -> Dict[str, Any]:
        key_col = table.columns[table.key]
        if not res.resolved(key_col):
            return {"table": table.name, "status": "skipped", "reason": f"key '{table.key}' not in source"}
        cols = spec.table_columns(table)
        attrs = [c for c in cols if c.name != table.key and res.resolved(c)]
        k = q(table.key)
        agg = ", ".join(
            f"arg_min({q(c.name)}, _source_row) FILTER (WHERE {q(c.name)} IS NOT NULL) AS {q(c.name)}"
            for c in attrs
        )
        select_cols = k + (", " + agg if agg else "")
        self.conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE _dim_new AS
            SELECT {select_cols}
            FROM ({projection_sql(spec, table, res)}) src
            WHERE {k} IS NOT NULL
            GROUP BY {k}
        """)
        names = [table.key] + [c.name for c in attrs]
        col_list = ", ".join(q(n) for n in names)
        before = self.conn.execute(f"SELECT COUNT(*) FROM {q(table.name)}").fetchone()[0]
        self.conn.execute(f"""
            INSERT INTO {q(table.name)} ({col_list}, _source_id)
            SELECT {col_list}, {_sql_str(sid)} FROM _dim_new n
            WHERE NOT EXISTS (SELECT 1 FROM {q(table.name)} d WHERE d.{k} = n.{k})
        """)
        inserted = self.conn.execute(f"SELECT COUNT(*) FROM {q(table.name)}").fetchone()[0] - before
        if attrs:
            sets = ", ".join(f"{q(c.name)} = COALESCE(d.{q(c.name)}, n.{q(c.name)})" for c in attrs)
            self.conn.execute(f"""
                UPDATE {q(table.name)} AS d SET {sets}
                FROM _dim_new n WHERE d.{k} = n.{k}
            """)
        seen = self.conn.execute("SELECT COUNT(*) FROM _dim_new").fetchone()[0]
        self.conn.execute("DROP TABLE IF EXISTS _dim_new")
        return {"table": table.name, "status": "loaded", "entities_in_source": seen,
                "new_entities": inserted, "columns_loaded": len(names)}

    def _load_fact(self, spec: SchemaSpec, table: TableSpec, res: Resolution, sid: str) -> Dict[str, Any]:
        if not res.resolved(spec.key_column(spec.spine_table)):
            return {"table": table.name, "status": "skipped", "reason": "spine key not in source"}
        where = fact_row_filter(spec, table, res)
        if where is None:
            return {"table": table.name, "status": "skipped", "reason": "no fact columns in source"}
        names = [c.name for c in spec.table_columns(table)]
        col_list = ", ".join(q(n) for n in names)
        self.conn.execute(f"DELETE FROM {q(table.name)} WHERE _source_id = ?", [sid])
        self.conn.execute(f"""
            INSERT INTO {q(table.name)} ({col_list}, _source_id, _source_row)
            SELECT {col_list}, {_sql_str(sid)}, MIN(_source_row)
            FROM ({projection_sql(spec, table, res)}) src
            WHERE {where}
            GROUP BY {col_list}
        """)
        n = self.conn.execute(f"SELECT COUNT(*) FROM {q(table.name)} WHERE _source_id = ?", [sid]).fetchone()[0]
        return {"table": table.name, "status": "loaded", "rows": n}

    def _record_source(self, sid: str, name: str, path: str, spec: SchemaSpec,
                       is_synthetic: bool, row_count: int, column_count: int):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.conn.execute(f"DELETE FROM {DATA_SOURCE_TABLE} WHERE source_id = ?", [sid])
        self.conn.execute(
            f"INSERT INTO {DATA_SOURCE_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [sid, name, Path(path).name, digest, spec.schema_id,
             hashlib.sha256(spec.to_yaml().encode("utf-8")).hexdigest(),
             is_synthetic, row_count, column_count, datetime.now()],
        )

    def load(self, spec: SchemaSpec, path: str, source_name: str, is_synthetic: bool = False,
             force: bool = False) -> Dict[str, Any]:
        """
        Stage -> preflight gates -> create/extend tables -> dimensions -> spine -> facts -> checks.
        Refuses to load when a gate fails unless `force` is set.
        """
        from olap_swifter.schema.checks import run_postload, run_preflight, summarize

        sid = source_id_for(source_name)
        staging = self.stage_csv(path, sid)
        res = resolve_source(spec, self.conn, staging)
        preflight = run_preflight(self.conn, spec, res)
        pre_summary = summarize(preflight)
        report: Dict[str, Any] = {
            "source_id": sid, "schema": spec.ref, "staging_table": staging,
            "unmapped_variables": res.unmapped, "excluded_variables": res.excluded_raw,
            "preflight": pre_summary,
        }
        if not pre_summary["all_passed"] and not force:
            report.update({"loaded": False, "reason": "preflight gates failed; fix the schema or pass force"})
            return report

        report["ddl_changes"] = self.ensure_tables(spec)
        ordered = ([t for t in spec.dimensions if t.name != spec.spine_table]
                   + [spec.tables[spec.spine_table]] + spec.facts)
        tables = []
        for t in ordered:
            loader = self._load_fact if t.is_fact else self._load_dimension
            tables.append(loader(spec, t, res, sid))
        row_count = self.conn.execute(f"SELECT COUNT(*) FROM {q(staging)}").fetchone()[0]
        self._record_source(sid, source_name, path, spec, is_synthetic, row_count, len(res.raw_columns) - 1)
        report["tables"] = tables
        report["postload"] = summarize(run_postload(self.conn, spec, res, sid))
        report["loaded"] = True
        return report
