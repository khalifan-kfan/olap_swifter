"""
Schema-Driven Synthetic Data.
Generates a dummy wide CSV that looks like a raw export for any schema, so the
model and the ETL can be proven end to end before real data is touched.

Values come only from the spec (allowed codes, ranges, types). Entities are
generated once and reused, so dimension attributes stay consistent per key,
and visits with several fact rows repeat their other values across rows the
way real flat exports do — which exercises the loader's de-duplication.

Per-table knobs in the YAML (optional):
    synthetic: { rows: 40 }            # dimensions: entity pool size
    synthetic: { per_visit: [0, 3] }   # facts: rows per visit (min, max)
"""

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from olap_swifter.schema.spec import ColumnSpec, SchemaSpec, TableSpec

NULL_RATE = 0.05
# Dummy data proves the model and the ETL; it does not need volume. Capping it
# keeps loads fast and keeps every result the agent reads small.
DEFAULT_MAX_ROWS = 400
DEFAULT_DATE_RANGE = ("2024-01-01", "2025-12-31")


def _to_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


class SyntheticGenerator:
    def __init__(self, spec: SchemaSpec, seed: int = 7):
        self.spec = spec
        self.rng = random.Random(seed)
        self.pools: Dict[str, List[Dict[str, Any]]] = {}
        # Raw-label columns read by a crosswalk draw their values from its general
        # (unscoped) entries, so dummy data exercises the mapping.
        self.label_values: Dict[str, List[str]] = {}
        for c in spec.all_columns():
            if c.crosswalk:
                xw = spec.crosswalks.get(c.crosswalk["file"]) or {"entries": []}
                values = [e["value"] for e in xw["entries"] if e["maps_to"] and not e["scope"]]
                if values:
                    self.label_values.setdefault(c.crosswalk["from"], []).extend(values)

    # ---------- values ----------

    def _key(self, col: ColumnSpec, i: int) -> Any:
        if col.type == "BIGINT":
            return i
        prefix = col.name.upper().replace("_ID", "").replace("_", "")[:6] or "ID"
        # Schema tag keeps dummy sets of different schemas from sharing keys.
        return f"{self.spec.schema_id[:3].upper()}-{prefix}-{i:05d}"

    def value(self, col: ColumnSpec, allow_null: bool = True) -> Any:
        r = self.rng
        if allow_null and not col.required and r.random() < NULL_RATE:
            return None
        if col.name in self.label_values:
            return r.choice(self.label_values[col.name])
        if col.allowed:
            return r.choice(col.allowed)
        if col.type == "BOOLEAN":
            return r.choice([True, False])
        if col.type == "DOUBLE":
            lo, hi = col.range or (0, 100)
            return round(r.uniform(float(lo if lo is not None else 0), float(hi if hi is not None else 100)), 2)
        if col.type == "BIGINT":
            lo, hi = col.range or (0, 100)
            return r.randint(int(lo if lo is not None else 0), int(hi if hi is not None else 100))
        if col.type in ("DATE", "TIMESTAMP"):
            lo, hi = col.range or DEFAULT_DATE_RANGE
            lo_d, hi_d = _to_date(lo), _to_date(hi)
            d = lo_d + timedelta(days=r.randint(0, max(0, (hi_d - lo_d).days)))
            if col.type == "DATE":
                return d.isoformat()
            return f"{d.isoformat()} {r.randint(0, 23):02d}:{r.randint(0, 59):02d}:00"
        return f"{col.name}_{r.randint(1, 6)}"

    def _row_values(self, cols: List[ColumnSpec]) -> Dict[str, Any]:
        """Raw-variable values for a set of columns (expr columns emit their inputs)."""
        out: Dict[str, Any] = {}
        for c in cols:
            if c.crosswalk:
                continue  # derived in the ETL from its raw-label column
            if c.expr:
                for name in c.inputs:
                    out[name] = self.rng.choice(c.input_examples) if c.input_examples else f"{name}_{self.rng.randint(1, 6)}"
            else:
                out[c.name] = self.value(c)
        return out

    # ---------- entities ----------

    def _dim_order(self) -> List[TableSpec]:
        done, order = set(), []

        def visit(t: TableSpec):
            if t.name in done:
                return
            for l in t.links:
                visit(self.spec.tables[l])
            done.add(t.name)
            order.append(t)

        for d in self.spec.dimensions:
            visit(d)
        return order

    def _build_pools(self, n_visits: int):
        for dim in self._dim_order():
            if dim.name == self.spec.spine_table:
                size = n_visits
            else:
                size = int(dim.synthetic.get("rows") or max(5, n_visits // 4))
            key_col = dim.columns[dim.key]
            pool = []
            for i in range(1, size + 1):
                row = {dim.key: self._key(key_col, i)}
                for link in dim.links:
                    row.update(self.rng.choice(self.pools[link]))
                own = [c for c in dim.columns.values() if c.name != dim.key]
                row.update(self._row_values(own))
                pool.append(row)
            self.pools[dim.name] = pool

    def _fact_rows(self, fact: TableSpec) -> List[Dict[str, Any]]:
        lo, hi = fact.synthetic.get("per_visit") or ([1, 1] if fact.name == self.spec.main_fact else [0, 2])
        count = self.rng.randint(int(lo), int(hi))
        if set(fact.grain) <= {self.spec.spine_key}:
            count = min(count, 1)
        links = [l for l in fact.links if l != self.spec.spine_table]
        picks = {l: self.rng.sample(self.pools[l], min(count, len(self.pools[l]))) for l in links}
        if links:
            count = min([count] + [len(p) for p in picks.values()])
        rows = []
        for i in range(count):
            row: Dict[str, Any] = {}
            for l in links:
                row.update(picks[l][i])
            own = self._row_values(list(fact.columns.values()))
            if own and all(v is None for v in own.values()):
                own = self._row_values(list(fact.columns.values()))  # a fact row needs content
            row.update(own)
            rows.append(row)
        return rows

    # ---------- output ----------

    def generate(self, n_visits: int, max_rows: Optional[int] = DEFAULT_MAX_ROWS) -> List[Dict[str, Any]]:
        """Wide rows for up to n_visits visits, stopping before max_rows is exceeded (None = no cap)."""
        self._build_pools(n_visits)
        wide: List[Dict[str, Any]] = []
        for visit in self.pools[self.spec.spine_table]:
            per_fact = [self._fact_rows(f) for f in self.spec.facts]
            depth = max([1] + [len(r) for r in per_fact])
            if max_rows is not None and len(wide) + depth > max_rows:
                break  # whole visits only, so every visit's facts stay complete
            for i in range(depth):
                row = dict(visit)
                for rows in per_fact:
                    if rows:
                        row.update(rows[min(i, len(rows) - 1)])
                wide.append(row)
        return wide

    def columns(self) -> List[str]:
        names: List[str] = []
        ordered = self._dim_order()
        spine = [t for t in ordered if t.name == self.spec.spine_table]
        for t in spine + [t for t in ordered if t.name != self.spec.spine_table] + self.spec.facts:
            for c in self.spec.table_columns(t):
                if c.crosswalk:
                    continue
                for n in (c.inputs if c.expr else [c.name]):
                    if n not in names:
                        names.append(n)
        return names

    def catalogue(self) -> List[Dict[str, Any]]:
        rows, seen = [], set()
        for c in self.spec.all_columns():
            if c.crosswalk:
                continue
            if c.expr:
                for n in c.inputs:
                    if n not in seen:
                        seen.add(n)
                        rows.append({"variable": n, "description": f"raw input for {c.table}.{c.name}",
                                     "type": "VARCHAR", "allowed_values": ""})
                continue
            if c.name in seen:
                continue
            seen.add(c.name)
            rows.append({"variable": c.name, "description": c.description, "type": c.type,
                         "allowed_values": "|".join(str(a) for a in c.allowed) if c.allowed else ""})
        return rows


def write_synthetic_dataset(spec: SchemaSpec, out_dir: str, n_visits: int = 200, seed: int = 7,
                            max_categories: int = 20, min_category_count: int = 5,
                            max_rows: Optional[int] = DEFAULT_MAX_ROWS) -> Dict[str, Any]:
    """
    Writes `<schema>_synthetic.csv`, its catalogue and its EDA into `out_dir`.
    At most max_rows rows are written (whole visits only); pass None only when a
    test genuinely needs more volume. Returns paths and counts, never rows.
    """
    from olap_swifter.schema.profile import profile_csv

    gen = SyntheticGenerator(spec, seed=seed)
    rows = gen.generate(n_visits, max_rows=max_rows)
    visits = len({r[spec.spine_key] for r in rows})
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data_path = out / f"{spec.schema_id}_synthetic.csv"
    cols = gen.columns()
    with open(data_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})

    cat_path = out / f"{spec.schema_id}_synthetic_catalogue.csv"
    with open(cat_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["variable", "description", "type", "allowed_values"])
        w.writeheader()
        w.writerows(gen.catalogue())

    eda_path = out / f"{spec.schema_id}_synthetic_eda.yaml"
    eda = profile_csv(str(data_path), max_categories=max_categories, min_category_count=min_category_count)
    eda_path.write_text(yaml.safe_dump(eda, sort_keys=False), encoding="utf-8")

    return {"schema": spec.ref, "data": str(data_path), "catalogue": str(cat_path), "eda": str(eda_path),
            "visits": visits, "visits_requested": n_visits, "rows": len(rows), "max_rows": max_rows,
            "capped": visits < n_visits, "columns": len(cols)}
