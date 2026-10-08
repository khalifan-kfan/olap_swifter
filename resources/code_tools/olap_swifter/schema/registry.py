"""
Schema Registry.
Stores each built schema as one YAML file (`<schema_id>.yaml`) so a schema is
designed once and reused: new datasets are matched against the registry before
anything new is modelled, and extensions are written back into the same file.

All schemas share one warehouse, so a table name means the same table
everywhere. A schema that names an existing table reuses it (and may add
columns to it); it never redefines it.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from olap_swifter.schema.spec import SchemaSpec, SchemaSpecError, attach_crosswalks, load_spec_file, parse_spec

DEFAULT_CONFIG: Dict[str, Any] = {
    # A dataset whose variables are this different (fraction unmatched) from the
    # best registered schema gets a new schema instead of an extension.
    "new_schema_min_difference": 0.8,
    # EDA category tables hide labels seen fewer times than this (small-cell suppression).
    "eda_min_category_count": 5,
    # EDA lists category labels only for variables with at most this many distinct values.
    "eda_max_categories": 20,
    # Dummy data is capped at this many rows (whole visits) unless a test needs more.
    "dummy_max_rows": 400,
}


def default_schemas_dir() -> Path:
    """`$OLAP_SWIFTER_SCHEMAS_DIR`, else `./resources/schemas`, else the repo's resources/schemas."""
    env = os.environ.get("OLAP_SWIFTER_SCHEMAS_DIR")
    if env:
        return Path(env)
    cwd_candidate = Path.cwd() / "resources" / "schemas"
    if cwd_candidate.is_dir():
        return cwd_candidate
    return Path(__file__).resolve().parents[3] / "schemas"


def default_inputs_dir() -> Path:
    """`$OLAP_SWIFTER_INPUTS_DIR`, else `inputs/` beside the schemas directory."""
    env = os.environ.get("OLAP_SWIFTER_INPUTS_DIR")
    return Path(env) if env else default_schemas_dir().parent / "inputs"


class SchemaRegistry:
    """Loads and saves schema specs in a directory, one file per schema."""

    def __init__(self, schemas_dir: Optional[Path] = None):
        self.dir = Path(schemas_dir) if schemas_dir else default_schemas_dir()

    def config(self) -> Dict[str, Any]:
        cfg = dict(DEFAULT_CONFIG)
        path = self.dir / "config.yaml"
        if path.exists():
            cfg.update(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        return cfg

    def _paths(self) -> Dict[str, Path]:
        if not self.dir.is_dir():
            return {}
        return {p.stem: p for p in sorted(self.dir.glob("*.yaml")) if p.name != "config.yaml"}

    def ids(self) -> List[str]:
        return sorted(self._paths())

    def exists(self, schema_id: str) -> bool:
        return schema_id in self._paths()

    def path_for(self, schema_id: str) -> Path:
        path = self._paths().get(schema_id)
        if path is None:
            raise KeyError(f"schema '{schema_id}' is not registered in {self.dir}")
        return path

    def get(self, schema_id: str) -> SchemaSpec:
        return load_spec_file(self.path_for(schema_id))

    def all(self) -> List[SchemaSpec]:
        return [self.get(sid) for sid in self.ids()]

    def list(self) -> List[Dict[str, Any]]:
        return [{
            "schema_id": s.schema_id,
            "title": s.title,
            "main_fact": s.main_fact,
            "tables": len(s.tables),
            "columns": len(s.all_columns()),
            "datasets": sorted(s.datasets),
        } for s in self.all()]

    def save(self, spec: SchemaSpec) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{spec.schema_id}.yaml"
        path.write_text(spec.to_yaml(), encoding="utf-8")
        return path

    def register(self, spec_path: Path) -> Path:
        """Adds a brand-new schema written by the skill."""
        spec = attach_crosswalks(parse_spec(yaml.safe_load(Path(spec_path).read_text(encoding="utf-8"))), self.dir)
        if self.exists(spec.schema_id):
            raise SchemaSpecError(
                f"schema '{spec.schema_id}' already exists; extend it with a patch instead"
            )
        conflicts = self.table_conflicts(spec)
        if conflicts:
            raise SchemaSpecError("; ".join(conflicts))
        return self.save(spec)

    def table_owners(self) -> Dict[str, List[str]]:
        """Table name -> schemas that use it (reused tables list several)."""
        owners: Dict[str, List[str]] = {}
        for s in self.all():
            for t in s.tables:
                owners.setdefault(t, []).append(s.schema_id)
        return owners

    def table_conflicts(self, spec: SchemaSpec) -> List[str]:
        """
        A reused table must agree with every other schema that uses it:
        same kind, same key, and the same type for any column both define.
        """
        problems = []
        for other in self.all():
            if other.schema_id == spec.schema_id:
                continue
            for name, tbl in spec.tables.items():
                o_tbl = other.tables.get(name)
                if o_tbl is None:
                    continue
                if tbl.kind != o_tbl.kind:
                    problems.append(f"table '{name}' is a {tbl.kind} here but a {o_tbl.kind} in '{other.schema_id}'")
                if tbl.key != o_tbl.key:
                    problems.append(f"table '{name}' has key '{tbl.key}' here but '{o_tbl.key}' in '{other.schema_id}'")
                for cname, col in tbl.columns.items():
                    o_col = o_tbl.columns.get(cname)
                    if o_col and o_col.type != col.type:
                        problems.append(f"column {name}.{cname} is {col.type} here "
                                        f"but {o_col.type} in '{other.schema_id}'")
        return problems
