"""
Source EDA & Data Catalogues.
Describes a raw dataset without exposing its rows. The EDA carries names,
types and aggregate statistics (counts, null rates, numeric ranges, and
category counts with small cells suppressed), so the orchestrating skill can
model a real dataset it is not allowed to read. The catalogue (data
dictionary) is metadata supplied by the data owner: variable names,
descriptions, types and codes.
"""

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import yaml

from olap_swifter.paths import route
from olap_swifter.schema.spec import normalize_type

DICTIONARY_FIELD_ALIASES = {
    "name": ("variable", "variable_name", "name", "column", "column_name", "field", "field_name", "var"),
    "description": ("description", "label", "variable_label", "definition", "meaning", "question"),
    "type": ("type", "data_type", "datatype", "dtype", "format"),
    "allowed_values": ("allowed_values", "values", "codes", "value_labels", "categories", "levels", "options"),
    "units": ("units", "unit", "uom"),
}


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def profile_csv(path: str, conn: Optional[duckdb.DuckDBPyConnection] = None,
                max_categories: int = 0, min_category_count: int = 5) -> Dict[str, Any]:
    """
    Aggregate-only EDA of a CSV file. With `max_categories` > 0, low-cardinality
    text variables also list their category counts; labels seen fewer than
    `min_category_count` times are pooled as suppressed, so rare values that
    could identify a person never appear.
    """
    own = conn is None
    conn = conn or duckdb.connect()
    try:
        rel = f"read_csv_auto({_sql_str(path)}, sample_size=-1)"
        info = conn.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]
        variables = []
        for name, duck_type, *_ in info:
            q = f'"{name}"'
            stats: Dict[str, Any] = {}
            nulls, distinct = conn.execute(
                f"SELECT COUNT(*) - COUNT({q}), COUNT(DISTINCT {q}) FROM {rel}"
            ).fetchone()
            stats["null_count"] = int(nulls)
            stats["null_pct"] = round(100.0 * nulls / total, 2) if total else 0.0
            stats["distinct_count"] = int(distinct)
            norm = normalize_type(duck_type) or "VARCHAR"
            if norm in ("BIGINT", "DOUBLE"):
                mn, mx, avg = conn.execute(f"SELECT MIN({q}), MAX({q}), AVG({q}) FROM {rel}").fetchone()
                stats.update({"min": mn, "max": mx, "mean": round(avg, 4) if avg is not None else None})
            elif norm in ("DATE", "TIMESTAMP"):
                mn, mx = conn.execute(f"SELECT MIN({q}), MAX({q}) FROM {rel}").fetchone()
                stats.update({"min": str(mn) if mn is not None else None,
                              "max": str(mx) if mx is not None else None})
            elif norm == "VARCHAR":
                mn, mx = conn.execute(f"SELECT MIN(LENGTH({q})), MAX(LENGTH({q})) FROM {rel}").fetchone()
                stats.update({"min_length": mn, "max_length": mx})
            if max_categories and norm in ("VARCHAR", "BOOLEAN") and 0 < distinct <= max_categories:
                counts = conn.execute(
                    f"SELECT CAST({q} AS VARCHAR), COUNT(*) FROM {rel} WHERE {q} IS NOT NULL "
                    f"GROUP BY 1 ORDER BY 2 DESC, 1"
                ).fetchall()
                shown = {label: int(n) for label, n in counts if n >= min_category_count}
                hidden = [n for _, n in counts if n < min_category_count]
                stats["categories"] = shown
                if hidden:
                    stats["suppressed_categories"] = {"labels": len(hidden), "rows": int(sum(hidden))}
            stats["unique_per_row"] = bool(total and distinct == total and nulls == 0)
            variables.append({"name": name, "type": norm, "source_type": duck_type, "stats": stats})
        return {"path": str(path), "row_count": int(total), "column_count": len(variables),
                "variables": variables}
    finally:
        if own:
            conn.close()


def _pick(row: Dict[str, Any], field: str) -> Optional[Any]:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for alias in DICTIONARY_FIELD_ALIASES[field]:
        if alias in lowered and lowered[alias] not in (None, ""):
            return lowered[alias]
    return None


def _split_values(raw: Any) -> Optional[List[str]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    text = str(raw)
    for sep in ("|", ";", ","):
        if sep in text:
            return [v.strip() for v in text.split(sep) if v.strip()]
    return [text.strip()]


CATALOGUE_SUFFIXES = (".csv", ".yaml", ".yml")
EDA_SUFFIXES = (".yaml", ".yml", ".json", ".csv")

# Minimal CSV EDA: one row per variable. Headers are matched loosely; only
# `variable` is needed. `categories` is "label:count|label:count".
EDA_CSV_FIELD_ALIASES = {
    "name": ("variable", "variable_name", "name", "column", "field"),
    "type": ("type", "data_type", "dtype"),
    "rows": ("rows", "n", "row_count", "total"),
    "null_count": ("missing", "null_count", "n_missing", "nulls"),
    "null_pct": ("missing_pct", "null_pct", "pct_missing", "missing_percent"),
    "distinct_count": ("distinct", "distinct_count", "n_distinct", "unique"),
    "min": ("min", "minimum"),
    "max": ("max", "maximum"),
    "mean": ("mean", "average", "avg"),
    "categories": ("categories", "top_values", "values", "levels"),
}


def _num(raw: Any) -> Any:
    try:
        f = float(raw)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return raw


def _load_eda_csv(path: Path) -> Dict[str, Any]:
    """Reads a minimal CSV EDA into the same shape `profile_csv` produces."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    lower = lambda r: {str(k).strip().lower(): v for k, v in r.items() if k}  # noqa: E731
    variables, row_count = [], None
    for raw in rows:
        r = lower(raw)
        get = lambda f: next((r[a] for a in EDA_CSV_FIELD_ALIASES[f] if r.get(a) not in (None, "")), None)  # noqa: E731
        name = get("name")
        if not name:
            continue
        stats: Dict[str, Any] = {}
        for f in ("null_count", "null_pct", "distinct_count", "min", "max", "mean"):
            if get(f) is not None:
                stats[f] = _num(get(f))
        if get("categories"):
            cats = {}
            for part in str(get("categories")).split("|"):
                label, _, n = part.rpartition(":")
                if label:
                    cats[label.strip()] = _num(n)
            stats["categories"] = cats
        if get("rows") is not None:
            row_count = _num(get("rows"))
        raw_type = get("type")
        variables.append({"name": str(name).strip(), "type": normalize_type(raw_type) if raw_type else None,
                          "source_type": raw_type, "stats": stats})
    out = {"path": str(path), "column_count": len(variables), "variables": variables}
    if row_count is not None:
        out["row_count"] = row_count
    return out


EDA_CSV_COLUMNS = ["variable", "type", "rows", "missing", "missing_pct", "distinct", "min", "max", "mean",
                   "categories", "suppressed_rows"]


def write_eda(eda: Dict[str, Any], path: str) -> str:
    """Writes an EDA as YAML, or as a minimal CSV when `path` ends in .csv."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".csv":
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=EDA_CSV_COLUMNS)
            w.writeheader()
            w.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in eda_to_csv_rows(eda))
    else:
        p.write_text(yaml.safe_dump(eda, sort_keys=False), encoding="utf-8")
    return str(p)


def eda_to_csv_rows(eda: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flattens a `profile_csv` EDA into minimal CSV rows (one per variable)."""
    out = []
    for v in eda["variables"]:
        st = v.get("stats", {})
        cats = st.get("categories") or {}
        out.append({"variable": v["name"], "type": v.get("type"), "rows": eda.get("row_count"),
                    "missing": st.get("null_count"), "missing_pct": st.get("null_pct"),
                    "distinct": st.get("distinct_count"),
                    "min": st.get("min"), "max": st.get("max"),
                    "mean": st.get("mean"),
                    "categories": "|".join(f"{k}:{n}" for k, n in cats.items()),
                    "suppressed_rows": (st.get("suppressed_categories") or {}).get("rows")})
    return out


def files_in(folder: Path, suffixes) -> List[Path]:
    """Files of the given types directly in a folder, sorted, hidden files skipped."""
    return sorted(p for p in Path(folder).iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in suffixes)


def _merge_files(path: str, suffixes, load_one) -> Dict[str, Any]:
    """Loads every file in a folder and merges their variables by name; each variable lists its files."""
    files = files_in(Path(path), suffixes)
    if not files:
        raise FileNotFoundError(f"no {'/'.join(suffixes)} files in {path}")
    by_name: Dict[str, Dict[str, Any]] = {}
    parts = []
    for f in files:
        one = load_one(str(f))
        parts.append({"file": f.name, "variables": len(one.get("variables", [])),
                      **({"row_count": one["row_count"]} if "row_count" in one else {})})
        for v in one.get("variables", []):
            key = v["name"].strip().lower()
            if key not in by_name:
                by_name[key] = {**v, "files": []}
            by_name[key]["files"].append(f.stem)  # = the data file it describes
    return {"path": str(path), "files": parts, "column_count": len(by_name), "variables": list(by_name.values())}


def load_dictionary(path: str) -> Dict[str, Any]:
    """
    Reads a data dictionary (CSV, or YAML list/mapping) into the same variable
    shape as a profile. Column headers are matched loosely (variable/name/field,
    description/label, type/data_type, values/codes, units). A folder is read
    as several dictionaries of one dataset, merged by variable name.
    """
    p = Path(path)
    if p.is_dir():
        return _merge_files(path, CATALOGUE_SUFFIXES, load_dictionary)
    if p.suffix.lower() in (".yaml", ".yml"):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "variables" in data:
            data = data["variables"]
        if isinstance(data, dict):
            rows = [{"name": k, **(v or {})} for k, v in data.items()]
        else:
            rows = list(data or [])
    else:
        with open(p, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))

    variables = []
    for row in rows:
        name = _pick(row, "name")
        if not name:
            continue
        raw_type = _pick(row, "type")
        variables.append({
            "name": str(name).strip(),
            "type": normalize_type(raw_type) if raw_type else None,
            "declared_type": raw_type,
            "description": _pick(row, "description") or "",
            "allowed_values": _split_values(_pick(row, "allowed_values")),
            "units": _pick(row, "units"),
        })
    return {"path": str(path), "column_count": len(variables), "variables": variables}


YDATA_TYPES = {"Numeric": "DOUBLE", "Text": "VARCHAR", "Categorical": "VARCHAR", "Boolean": "BOOLEAN",
               "DateTime": "TIMESTAMP"}


def _eda_privacy() -> Dict[str, int]:
    """The EDA privacy thresholds from resources/config.yaml."""
    cfg = {"eda_max_categories": 20, "eda_min_category_count": 5}
    try:
        cfg.update(yaml.safe_load(route("config").read_text(encoding="utf-8")) or {})
    except FileNotFoundError:
        pass
    return {"max_categories": int(cfg["eda_max_categories"]),
            "min_category_count": int(cfg["eda_min_category_count"])}


def _is_ydata(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("variables"), dict) and "table" in data


def _load_eda_ydata(path: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reads a ydata-profiling JSON report into the same shape `profile_csv`
    produces. The report keeps every value's count (identifiers included) and
    may keep sample rows; only aggregates are taken, and category counts only
    for low-cardinality variables, with small cells pooled as suppressed.
    """
    privacy = _eda_privacy()
    variables = []
    for name, v in data["variables"].items():
        kind = v.get("type")
        norm = YDATA_TYPES.get(kind, "VARCHAR")
        if norm == "DOUBLE" and isinstance(v.get("min"), int) and isinstance(v.get("max"), int):
            norm = "BIGINT"
        nulls, distinct = int(v.get("n_missing") or 0), int(v.get("n_distinct") or 0)
        stats: Dict[str, Any] = {"null_count": nulls, "null_pct": round(100.0 * float(v.get("p_missing") or 0), 2),
                                 "distinct_count": distinct}
        if norm in ("BIGINT", "DOUBLE", "TIMESTAMP"):
            mn, mx, avg = v.get("min"), v.get("max"), v.get("mean")
            stats.update({"min": mn if norm != "TIMESTAMP" else str(mn), "max": mx if norm != "TIMESTAMP" else str(mx)})
            if norm != "TIMESTAMP":
                stats["mean"] = round(avg, 4) if isinstance(avg, (int, float)) else None
        counts = v.get("value_counts_without_nan")
        if norm in ("VARCHAR", "BOOLEAN") and isinstance(counts, dict) and 0 < distinct <= privacy["max_categories"]:
            shown = {str(k): int(n) for k, n in counts.items() if n >= privacy["min_category_count"]}
            hidden = [int(n) for n in counts.values() if n < privacy["min_category_count"]]
            stats["categories"] = shown
            if hidden:
                stats["suppressed_categories"] = {"labels": len(hidden), "rows": sum(hidden)}
        stats["unique_per_row"] = bool(v.get("is_unique") and nulls == 0)
        variables.append({"name": name, "type": norm, "source_type": f"ydata:{kind}", "stats": stats})
    out = {"path": str(path), "column_count": len(variables), "variables": variables, "made_by": "ydata-profiling"}
    if (data.get("table") or {}).get("n") is not None:
        out["row_count"] = int(data["table"]["n"])
    return out


def load_eda(path: str) -> Dict[str, Any]:
    """
    Reads an EDA (Exploratory Data Analysis) file. EDAs produced by
    `profile_csv` (YAML/JSON with `variables`), ydata-profiling JSON reports
    and minimal CSV EDAs (one row per variable) contribute statistics to
    matching; any other EDA format is accepted and recorded, for the skill to
    read, but adds no statistics.
    A folder holds one EDA per data file; their variables are merged by name.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"EDA not found: {path}")
    if p.is_dir():
        return _merge_files(path, EDA_SUFFIXES, load_eda)
    if p.suffix.lower() == ".csv":
        return _load_eda_csv(p)
    if p.suffix.lower() in (".yaml", ".yml", ".json"):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))  # YAML is a superset of JSON
        if isinstance(data, dict) and isinstance(data.get("variables"), list):
            return data
        if _is_ydata(data):
            return _load_eda_ydata(p, data)
    return {"path": str(path), "variables": [], "free_form": True}


def merge_profile_and_dictionary(profile: Optional[Dict[str, Any]],
                                 dictionary: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    One variable list for matching: dictionary descriptions/codes plus profile
    statistics. Profile types win when both exist, since they reflect the file.
    """
    by_name: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for src in (dictionary, profile):
        for v in (src or {}).get("variables", []):
            key = v["name"].strip().lower()
            if key not in by_name:
                by_name[key] = {"name": v["name"]}
                order.append(key)
            merged = by_name[key]
            for k, val in v.items():
                if val not in (None, "", []):
                    merged[k] = val
    return [by_name[k] for k in order]


# Answered by the user.
DESCRIPTION_REQUIRED = (
    ("dataset", "name"), ("dataset", "catalogue"), ("dataset", "eda"), ("study", "description"),
    ("study", "main_question"), ("unit_of_analysis", "one_row_is"), ("unit_of_analysis", "main_event"),
)
# Modelled by the skill from the catalogue, the EDA and the registry match.
SKILL_REQUIRED = (
    ("visit", "identifier"), ("relationship_to_existing", "schema"), ("relationship_to_existing", "decision"),
)


def empty_fields(data: Dict[str, Any], fields) -> List[str]:
    return [f"{a}.{b}" for a, b in fields if not (data.get(a) or {}).get(b)]


def load_description(path: str) -> Dict[str, Any]:
    """
    Reads a dataset description. `missing_required` are user answers still empty;
    `skill_to_fill` are the sections the skill must model before attaching.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {"path": str(path), "description": data, "missing_required": empty_fields(data, DESCRIPTION_REQUIRED),
            "skill_to_fill": empty_fields(data, SKILL_REQUIRED)}
