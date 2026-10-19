"""
Vocabularies and Crosswalks.
Sites name the same thing differently ("Paediatric", "Pediatric", "Children's
Ward", "Ward 4B"). The warehouse keeps each site's own label and adds a
standard category through a crosswalk file the skill writes:

    results_workspace/schemas/crosswalks/<name>.yaml
    entries:
      - {value: Pediatric, maps_to: Paediatric}
      - {value: Ward 4B, scope: HOSP-02, maps_to: Surgical}   # one site's own wording

`label_counts` lists the distinct labels of a vocabulary variable (with row
counts and the sites they appear at) so the skill can map them; it refuses
identifiers and free-text-like variables. `merge_crosswalk` adds labels not yet
in the crosswalk with an empty maps_to, keeping every existing mapping.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb
import yaml

from olap_swifter.schema.readers import as_csv, split_sheet
from olap_swifter.schema.profile import load_description
from olap_swifter.schema.spec import CROSSWALK_DIR, normalize_label, normalize_name

MAX_LABELS = 500
# A variable with more distinct values than this share of its rows (and more
# than FREE_TEXT_MIN_DISTINCT labels) looks like an identifier or free text.
FREE_TEXT_RATIO = 0.5
FREE_TEXT_MIN_DISTINCT = 30


class VocabularyError(ValueError):
    pass


def _sql_str(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _find(columns: List[str], variable: str) -> Optional[str]:
    return next((c for c in columns if normalize_name(c) == normalize_name(variable)), None)


def label_counts(data_files: List[str], variable: str, by: Optional[str] = None,
                 description: Optional[str] = None) -> Dict[str, Any]:
    """Distinct labels of `variable` across the data files, with rows and (optionally) the `by` values seen."""
    if description and Path(description).exists():
        privacy = (load_description(description)["description"].get("privacy") or {})
        blocked = {normalize_name(v) for v in (privacy.get("direct_identifiers") or []) +
                   (privacy.get("quasi_identifiers") or [])}
        if normalize_name(variable) in blocked:
            raise VocabularyError(f"{variable} is marked as an identifier in the description; its values are not listed")

    conn = duckdb.connect()
    parts, used = [], []
    try:
        for f in data_files:
            rel = f"read_csv_auto({_sql_str(as_csv(f))}, all_varchar=true, sample_size=-1)"
            cols = [r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
            col = _find(cols, variable)
            if col is None:
                continue
            by_col = _find(cols, by) if by else None
            by_sql = f'NULLIF(TRIM("{by_col}"), \'\')' if by_col else "NULL"
            parts.append(f'SELECT NULLIF(TRIM("{col}"), \'\') AS label, {by_sql} AS scope FROM {rel}')
            used.append(Path(split_sheet(f)[0]).name)
        if not parts:
            raise VocabularyError(f"no data file has a variable named {variable!r}")
        union = " UNION ALL ".join(parts)
        total, distinct = conn.execute(
            f"SELECT COUNT(label), COUNT(DISTINCT label) FROM ({union})").fetchone()
        if distinct > FREE_TEXT_MIN_DISTINCT and distinct > FREE_TEXT_RATIO * max(total, 1):
            raise VocabularyError(f"{variable} has {distinct} distinct values in {total} rows; it looks like an "
                                  "identifier or free text, not a vocabulary, so its values are not listed")
        if distinct > MAX_LABELS:
            raise VocabularyError(f"{variable} has {distinct} distinct values (more than {MAX_LABELS})")
        rows = conn.execute(f"""SELECT label, COUNT(*), LIST(DISTINCT scope ORDER BY scope)
            FROM ({union}) WHERE label IS NOT NULL GROUP BY label ORDER BY 2 DESC, 1""").fetchall()
    finally:
        conn.close()
    labels = [{"value": lbl, "rows": int(n), **({"seen_at": [s for s in seen if s is not None]} if by else {})}
              for lbl, n, seen in rows]
    return {"variable": variable, "by": by, "files": used, "rows_with_value": int(total),
            "distinct": int(distinct), "labels": labels}


def merge_crosswalk(path: Path, name: str, from_variable: str, labels: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Adds unseen labels (maps_to empty) to crosswalks/<name>.yaml, keeping existing entries and mappings."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    entries = list(data.get("entries") or [])
    # A label with only site-scoped entries is known too; a new site using it is caught by the load gate.
    known = {normalize_label(e["value"]) for e in entries}
    added = 0
    by_label = {normalize_label(l["value"]): l for l in labels}
    for e in entries:  # refresh counts on existing general entries
        hit = by_label.get(normalize_label(e["value"]))
        if hit and not e.get("scope"):
            e["rows"] = hit["rows"]
            if "seen_at" in hit:
                e["seen_at"] = hit["seen_at"]
    for norm, l in by_label.items():
        if norm not in known:
            entries.append({"value": l["value"], "maps_to": "", "rows": l["rows"],
                            **({"seen_at": l["seen_at"]} if "seen_at" in l else {})})
            added += 1
    out = {"name": name, "from_variable": data.get("from_variable") or from_variable, "entries": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# Crosswalk {name}: raw labels -> one standard value each (the allowed values of the\n"
              "# schema column that uses this crosswalk). Fill every empty maps_to. A label that means\n"
              "# something different at one site gets its own entry with `scope: <site code>`.\n"
              "# Add entries freely; change an existing maps_to only with the user's agreement,\n"
              "# since loaded data already carries it.\n")
    path.write_text(header + yaml.safe_dump(out, sort_keys=False, allow_unicode=True), encoding="utf-8")
    unmapped = sum(1 for e in entries if not e.get("maps_to"))
    return {"crosswalk": str(path), "entries": len(entries), "added": added, "unmapped": unmapped}


def crosswalk_path(schemas_dir: Path, name: str) -> Path:
    return Path(schemas_dir) / CROSSWALK_DIR / f"{normalize_name(name)}.yaml"
