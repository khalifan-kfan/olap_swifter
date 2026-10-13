"""
Schema Specification Model.
A schema is a YAML file that holds every modelling decision for a study:
which tables exist, which variables land where, the visit spine, the main fact,
and the rules (aliases, allowed codes, ranges, extraction expressions) used to
load raw sources into it. The Python code holds no domain knowledge; it only
reads, validates and executes these specs.
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml


TYPE_ALIASES = {
    "VARCHAR": "VARCHAR", "STRING": "VARCHAR", "TEXT": "VARCHAR", "STR": "VARCHAR",
    "CATEGORICAL": "VARCHAR", "CATEGORY": "VARCHAR", "CHAR": "VARCHAR", "OBJECT": "VARCHAR",
    "BIGINT": "BIGINT", "INTEGER": "BIGINT", "INT": "BIGINT", "INT64": "BIGINT", "COUNT": "BIGINT",
    "DOUBLE": "DOUBLE", "FLOAT": "DOUBLE", "FLOAT64": "DOUBLE", "NUMERIC": "DOUBLE",
    "DECIMAL": "DOUBLE", "NUMBER": "DOUBLE", "REAL": "DOUBLE", "CONTINUOUS": "DOUBLE",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP", "DATETIME": "TIMESTAMP",
    "BOOLEAN": "BOOLEAN", "BOOL": "BOOLEAN", "BINARY": "BOOLEAN",
}

TYPE_FAMILY = {
    "VARCHAR": "text", "BIGINT": "numeric", "DOUBLE": "numeric",
    "DATE": "temporal", "TIMESTAMP": "temporal", "BOOLEAN": "boolean",
}

ROLES = {"key", "attribute", "measure"}
KINDS = {"dimension", "fact"}

# Columns the loader adds to every table for lineage; reserved in specs.
LINEAGE_COLUMNS = ("_source_id", "_source_row")


class SchemaSpecError(ValueError):
    """Raised when a schema YAML is malformed or internally inconsistent."""


def normalize_name(name: str) -> str:
    """Canonical form used to compare variable names, aliases and dictionary entries."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def normalize_type(raw_type: Optional[str]) -> Optional[str]:
    if raw_type is None:
        return None
    key = str(raw_type).strip().upper().split("(")[0]
    return TYPE_ALIASES.get(key)


def types_compatible(schema_type: str, source_type: Optional[str]) -> bool:
    """Whether a source variable of `source_type` can be loaded into a `schema_type` column."""
    if source_type is None or schema_type == "VARCHAR":
        return True
    if schema_type == "BIGINT":
        return source_type in ("BIGINT", "BOOLEAN")
    s_fam, src_fam = TYPE_FAMILY[schema_type], TYPE_FAMILY.get(source_type)
    if s_fam == src_fam:
        return True
    # Dates frequently arrive as text; the load step casts and the checks count failures.
    return s_fam == "temporal" and src_fam == "text"


@dataclass
class ColumnSpec:
    name: str
    type: str
    role: str
    table: str
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    source: Optional[str] = None
    expr: Optional[str] = None
    inputs: List[str] = field(default_factory=list)
    input_examples: List[str] = field(default_factory=list)
    required: bool = False
    allowed: Optional[List[Any]] = None
    range: Optional[List[Any]] = None
    pii: bool = False
    datasets: List[str] = field(default_factory=list)
    # Standardised category derived from another column through a crosswalk file:
    # {file: <crosswalks/<file>.yaml>, from: <column with the raw label>, scope: <optional column>}
    crosswalk: Optional[Dict[str, str]] = None

    def match_names(self) -> List[str]:
        """Normalized names a raw variable may carry to map onto this column."""
        if self.crosswalk:
            return []  # derived from its `from` column, never read directly
        names = [self.name] + list(self.aliases)
        if self.source:
            names.insert(0, self.source)
        return [normalize_name(n) for n in names]

    def to_yaml(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": self.type, "role": self.role}
        for attr in ("description", "aliases", "source", "expr", "inputs", "input_examples",
                     "required", "allowed", "range", "pii", "crosswalk", "datasets"):
            val = getattr(self, attr)
            if val not in (None, "", [], False):
                out[attr] = val
        return out


@dataclass
class TableSpec:
    name: str
    kind: str
    columns: Dict[str, ColumnSpec]
    key: Optional[str] = None
    links: List[str] = field(default_factory=list)
    grain: List[str] = field(default_factory=list)
    description: str = ""
    synthetic: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_fact(self) -> bool:
        return self.kind == "fact"

    def to_yaml(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind}
        if self.description:
            out["description"] = self.description
        if self.key:
            out["key"] = self.key
        if self.links:
            out["links"] = list(self.links)
        if self.grain:
            out["grain"] = list(self.grain)
        if self.synthetic:
            out["synthetic"] = dict(self.synthetic)
        out["columns"] = {c.name: c.to_yaml() for c in self.columns.values()}
        return out


@dataclass
class SchemaSpec:
    schema_id: str
    title: str
    study: str
    spine_table: str
    spine_key: str
    main_fact: str
    tables: Dict[str, TableSpec]
    excluded_variables: Dict[str, str] = field(default_factory=dict)
    datasets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    changelog: List[str] = field(default_factory=list)
    # Loaded from crosswalks/<file>.yaml beside the schema file; not part of the YAML itself.
    crosswalks: Dict[str, Dict[str, Any]] = field(default_factory=dict, repr=False)

    # ---------- derived structure ----------

    @property
    def dimensions(self) -> List[TableSpec]:
        return [t for t in self.tables.values() if not t.is_fact]

    @property
    def facts(self) -> List[TableSpec]:
        return [t for t in self.tables.values() if t.is_fact]

    def table_links(self, table: TableSpec) -> List[str]:
        """Dimensions a table references directly. Every fact references the spine."""
        links = list(table.links)
        if table.is_fact and self.spine_table not in links:
            links.insert(0, self.spine_table)
        return links

    def key_column(self, dim_name: str) -> ColumnSpec:
        dim = self.tables[dim_name]
        return dim.columns[dim.key]

    def table_columns(self, table: TableSpec) -> List[ColumnSpec]:
        """All physical columns of a table: foreign keys from links first, then own columns."""
        cols: List[ColumnSpec] = []
        seen = set()
        if not table.is_fact and table.key:
            cols.append(table.columns[table.key])
            seen.add(table.key)
        for link in self.table_links(table):
            kc = self.key_column(link)
            if kc.name not in seen:
                cols.append(kc)
                seen.add(kc.name)
        for c in table.columns.values():
            if c.name not in seen:
                cols.append(c)
                seen.add(c.name)
        return cols

    def all_columns(self) -> List[ColumnSpec]:
        return [c for t in self.tables.values() for c in t.columns.values()]

    def relationships(self) -> List[Dict[str, str]]:
        rels = []
        for t in self.tables.values():
            for link in self.table_links(t):
                rels.append({"from": t.name, "to": link, "key": self.tables[link].key})
        return rels

    def metadata(self) -> Dict[str, Any]:
        """Shape consumed by JoinGraph and the validators."""
        return {
            "model_type": "constellation",
            "schema_id": self.schema_id,
            "central_spine": self.spine_table,
            "spine_key": self.spine_key,
            "main_fact": self.main_fact,
            "dimensions": [d.name for d in self.dimensions],
            "facts": [f.name for f in self.facts],
            "relationships": self.relationships(),
        }

    # ---------- serialisation ----------

    @property
    def ref(self) -> str:
        return self.schema_id

    def dataset_for_source(self, source_id: str) -> Dict[str, Any]:
        """
        The attached dataset a source belongs to: the source is named after the
        dataset, or `<dataset>_<data file>` when a dataset has several files
        (the longest matching dataset name wins).
        """
        name = self.dataset_name_for_source(source_id)
        return (self.datasets[name] or {}) if name else {}

    def dataset_name_for_source(self, source_id: str) -> Optional[str]:
        sid = normalize_name(source_id)
        hits = [n for n in self.datasets if sid == normalize_name(n) or sid.startswith(normalize_name(n) + "_")]
        return max(hits, key=len) if hits else None

    def dataset_columns(self, dataset: str) -> List[str]:
        """`table.column` for every column fed by `dataset`."""
        return [f"{c.table}.{c.name}" for c in self.all_columns() if dataset in c.datasets]

    def to_yaml_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "schema_id": self.schema_id,
            "title": self.title,
            "study": self.study,
            "spine": {"table": self.spine_table, "key": self.spine_key},
            "main_fact": self.main_fact,
        }
        if self.datasets:
            out["datasets"] = dict(self.datasets)
        if self.changelog:
            out["changelog"] = list(self.changelog)
        if self.excluded_variables:
            out["excluded_variables"] = dict(self.excluded_variables)
        out["tables"] = {t.name: t.to_yaml() for t in self.tables.values()}
        return out

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_yaml_dict(), sort_keys=False, allow_unicode=True, width=100)


def _parse_column(table: str, name: str, raw: Dict[str, Any]) -> ColumnSpec:
    if not isinstance(raw, dict):
        raise SchemaSpecError(f"{table}.{name}: column spec must be a mapping")
    col_type = normalize_type(raw.get("type"))
    if col_type is None:
        raise SchemaSpecError(f"{table}.{name}: unknown type {raw.get('type')!r}")
    role = raw.get("role", "attribute")
    if role not in ROLES:
        raise SchemaSpecError(f"{table}.{name}: role must be one of {sorted(ROLES)}, got {role!r}")
    if name in LINEAGE_COLUMNS:
        raise SchemaSpecError(f"{table}.{name}: column name is reserved for lineage")
    rng = raw.get("range")
    if rng is not None and (not isinstance(rng, list) or len(rng) != 2):
        raise SchemaSpecError(f"{table}.{name}: range must be [min, max]")
    if raw.get("expr") and not raw.get("inputs"):
        raise SchemaSpecError(f"{table}.{name}: an expr column must list the raw variables it reads in 'inputs'")
    if raw.get("expr") and ";" in str(raw["expr"]):
        raise SchemaSpecError(f"{table}.{name}: expr must be a single SQL expression")
    xw = raw.get("crosswalk")
    if xw is not None:
        if not isinstance(xw, dict) or not xw.get("file") or not xw.get("from"):
            raise SchemaSpecError(f"{table}.{name}: crosswalk needs 'file' and 'from' (the column holding the raw label)")
        if raw.get("expr") or raw.get("aliases") or raw.get("source"):
            raise SchemaSpecError(f"{table}.{name}: a crosswalk column cannot also have expr, aliases or source")
        if normalize_type(raw.get("type")) != "VARCHAR":
            raise SchemaSpecError(f"{table}.{name}: a crosswalk column must be VARCHAR")
        xw = {k: str(xw[k]) for k in ("file", "from", "scope") if xw.get(k)}
    return ColumnSpec(
        name=name, type=col_type, role=role, table=table,
        description=raw.get("description", ""),
        aliases=list(raw.get("aliases") or []),
        source=raw.get("source"),
        expr=raw.get("expr"),
        inputs=list(raw.get("inputs") or []),
        input_examples=[str(v) for v in (raw.get("input_examples") or [])],
        required=bool(raw.get("required", False)),
        allowed=raw.get("allowed"),
        range=rng,
        pii=bool(raw.get("pii", False)),
        datasets=list(raw.get("datasets") or []),
        crosswalk=xw,
    )


def parse_spec(data: Dict[str, Any]) -> SchemaSpec:
    """Builds a SchemaSpec from parsed YAML and validates it. Raises SchemaSpecError."""
    if not isinstance(data, dict):
        raise SchemaSpecError("schema file must contain a mapping")
    for req in ("schema_id", "spine", "main_fact", "tables"):
        if req not in data:
            raise SchemaSpecError(f"missing top-level field '{req}'")
    schema_id = str(data["schema_id"])
    if normalize_name(schema_id) != schema_id:
        raise SchemaSpecError(f"schema_id must be snake_case, got {schema_id!r}")

    tables: Dict[str, TableSpec] = {}
    for t_name, t_raw in (data.get("tables") or {}).items():
        if not isinstance(t_raw, dict):
            raise SchemaSpecError(f"{t_name}: table spec must be a mapping")
        kind = t_raw.get("kind")
        if kind not in KINDS:
            raise SchemaSpecError(f"{t_name}: kind must be 'dimension' or 'fact'")
        cols = {c: _parse_column(t_name, c, c_raw) for c, c_raw in (t_raw.get("columns") or {}).items()}
        tables[t_name] = TableSpec(
            name=t_name, kind=kind, columns=cols,
            key=t_raw.get("key"),
            links=list(t_raw.get("links") or []),
            grain=list(t_raw.get("grain") or []),
            description=t_raw.get("description", ""),
            synthetic=dict(t_raw.get("synthetic") or {}),
        )

    spine = data["spine"] or {}
    spec = SchemaSpec(
        schema_id=schema_id,
        title=data.get("title", schema_id),
        study=data.get("study", ""),
        spine_table=spine.get("table"),
        spine_key=spine.get("key"),
        main_fact=data["main_fact"],
        tables=tables,
        excluded_variables=dict(data.get("excluded_variables") or {}),
        datasets=dict(data.get("datasets") or {}),
        changelog=list(data.get("changelog") or []),
    )
    errors = validate_spec(spec)
    if errors:
        raise SchemaSpecError("; ".join(errors))
    return spec


def validate_spec(spec: SchemaSpec) -> List[str]:
    """Structural checks. Returns a list of human-readable problems (empty when valid)."""
    errors: List[str] = []
    t = spec.tables

    if spec.spine_table not in t:
        errors.append(f"spine table '{spec.spine_table}' is not defined")
    elif t[spec.spine_table].is_fact:
        errors.append("spine table must be a dimension")
    elif t[spec.spine_table].key != spec.spine_key:
        errors.append(f"spine key '{spec.spine_key}' must be the key of '{spec.spine_table}'")

    if spec.main_fact not in t or not t[spec.main_fact].is_fact:
        errors.append(f"main_fact '{spec.main_fact}' must be a defined fact table")

    for tbl in t.values():
        if not tbl.is_fact:
            if not tbl.key:
                errors.append(f"{tbl.name}: dimension needs a key")
            elif tbl.key not in tbl.columns:
                errors.append(f"{tbl.name}: key '{tbl.key}' is not one of its columns")
            elif tbl.columns[tbl.key].role != "key":
                errors.append(f"{tbl.name}.{tbl.key}: key column must have role 'key'")
        elif tbl.key:
            errors.append(f"{tbl.name}: facts do not take a key; use grain")
        for link in tbl.links:
            if link not in t:
                errors.append(f"{tbl.name}: links to undefined table '{link}'")
            elif t[link].is_fact:
                errors.append(f"{tbl.name}: may only link to dimensions, not '{link}'")
        for c in tbl.columns.values():
            if c.role == "key" and (tbl.is_fact or c.name != tbl.key):
                errors.append(f"{tbl.name}.{c.name}: role 'key' is only for a dimension's own key "
                              "(reference other dimensions through 'links')")
            if c.role == "measure" and TYPE_FAMILY[c.type] != "numeric":
                errors.append(f"{tbl.name}.{c.name}: measures must be numeric")

    if errors:
        return errors

    # Grain columns must exist on the physical table.
    for tbl in spec.facts:
        physical = {c.name for c in spec.table_columns(tbl)}
        for g in tbl.grain:
            if g not in physical:
                errors.append(f"{tbl.name}: grain column '{g}' is not on the table")

    # A raw variable name may only map to one column, or loads become ambiguous.
    owners: Dict[str, str] = {}
    for c in spec.all_columns():
        for n in set(c.match_names()):
            where = f"{c.table}.{c.name}"
            if n in owners and owners[n] != where:
                errors.append(f"variable name '{n}' maps to both {owners[n]} and {where}")
            owners[n] = where

    # Links must not form cycles (dimensions hang off the spine, not each other in a loop).
    graph = {name: [l for l in tbl.links] for name, tbl in t.items() if not tbl.is_fact}
    visiting, done = set(), set()

    def visit(n: str) -> bool:
        if n in done:
            return False
        if n in visiting:
            return True
        visiting.add(n)
        cyclic = any(visit(m) for m in graph.get(n, []))
        visiting.discard(n)
        done.add(n)
        return cyclic

    if any(visit(n) for n in graph):
        errors.append("dimension links form a cycle")
    return errors


CROSSWALK_DIR = "crosswalks"


def normalize_label(value: Any) -> str:
    """Case- and whitespace-insensitive form of a raw label (mirrors label_sql)."""
    return " ".join(str(value).split()).lower()


def label_sql(sql: str) -> str:
    """SQL twin of normalize_label."""
    return f"LOWER(TRIM(REGEXP_REPLACE(CAST({sql} AS VARCHAR), '\\s+', ' ', 'g')))"


def load_crosswalk(path: Path, allowed: Optional[List[Any]] = None) -> Dict[str, Any]:
    """
    Reads crosswalks/<name>.yaml: entries of {value, maps_to, scope?, rows?}.
    Entries with an empty maps_to are labels still waiting to be mapped.
    """
    if not Path(path).exists():
        return {"path": str(path), "entries": [], "missing": True}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries, seen = [], set()
    for e in data.get("entries") or []:
        if not isinstance(e, dict) or e.get("value") in (None, ""):
            raise SchemaSpecError(f"{path}: every entry needs a 'value'")
        key = (normalize_label(e["value"]), normalize_label(e["scope"]) if e.get("scope") else None)
        if key in seen:
            raise SchemaSpecError(f"{path}: duplicate entry for {e['value']!r} (scope {e.get('scope')})")
        seen.add(key)
        target = e.get("maps_to")
        if target not in (None, "") and allowed and str(target) not in {str(a) for a in allowed}:
            raise SchemaSpecError(f"{path}: {e['value']!r} maps to {target!r}, which is not an allowed value "
                                  f"({', '.join(str(a) for a in allowed)})")
        entries.append({"value": str(e["value"]), "scope": str(e["scope"]) if e.get("scope") else None,
                        "maps_to": str(target) if target not in (None, "") else None})
    return {"path": str(path), "entries": entries, "missing": False}


def attach_crosswalks(spec: SchemaSpec, schemas_dir: Path) -> SchemaSpec:
    """Loads the crosswalk file of every crosswalk column (missing files load as empty)."""
    names = {c.name for c in spec.all_columns()}
    for c in spec.all_columns():
        if not c.crosswalk:
            continue
        for ref in ("from", "scope"):
            if c.crosswalk.get(ref) and c.crosswalk[ref] not in names:
                raise SchemaSpecError(f"{c.table}.{c.name}: crosswalk {ref} column '{c.crosswalk[ref]}' "
                                      "is not a column of this schema")
        spec.crosswalks[c.crosswalk["file"]] = load_crosswalk(
            Path(schemas_dir) / CROSSWALK_DIR / f"{c.crosswalk['file']}.yaml", c.allowed)
    return spec


def load_spec_file(path) -> SchemaSpec:
    with open(path, "r", encoding="utf-8") as fh:
        spec = parse_spec(yaml.safe_load(fh))
    return attach_crosswalks(spec, Path(path).parent)


def column_index(spec: SchemaSpec) -> Dict[str, Tuple[str, str]]:
    """Normalized variable name -> (table, column) for every name a column answers to."""
    idx: Dict[str, Tuple[str, str]] = {}
    for c in spec.all_columns():
        for n in c.match_names():
            idx[n] = (c.table, c.name)
    return idx
