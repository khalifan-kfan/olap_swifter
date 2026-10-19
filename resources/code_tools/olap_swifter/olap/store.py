"""
Embedded DuckDB OLAP Store.
Holds the warehouse connection and fills it with schema-driven dummy data.
Table structure comes from the registered schema YAML (results_workspace/schemas), not
from code, so the same store serves AMR, HIV or any other registered model.
"""

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import duckdb
import pandas as pd

from olap_swifter.schema.materializer import WarehouseLoader
from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.schema.synthetic import write_synthetic_dataset

DEFAULT_SCHEMA = None  # the only registered schema (SchemaRegistry.default_id)


class MedicalOLAPStore:
    """Manages the embedded DuckDB analytical warehouse."""

    def __init__(self, db_path: Optional[str] = None, registry: Optional[SchemaRegistry] = None):
        self.db_path = db_path or ":memory:"
        self.conn = duckdb.connect(self.db_path)
        self.registry = registry or SchemaRegistry()
        self.loader = WarehouseLoader(self.conn)
        self.last_load: Optional[Dict[str, Any]] = None

    def populate_synthetic_data(self, num_visits: int = 1000, schema_id: Optional[str] = DEFAULT_SCHEMA,
                                seed: int = 7) -> Dict[str, Any]:
        """Generates dummy data for a registered schema (default: the only one) and loads it through the ETL."""
        schema_id = self.registry.default_id(schema_id)
        spec = self.registry.get(schema_id)
        with tempfile.TemporaryDirectory() as tmp:
            out = write_synthetic_dataset(spec, tmp, n_visits=num_visits, seed=seed,
                                          max_rows=int(self.registry.config()["dummy_max_rows"]))
            report = self.loader.load(spec, out["data"], f"{schema_id}_dummy", is_synthetic=True)
        if not report["loaded"]:
            raise RuntimeError(f"dummy data for {schema_id} failed preflight: {report['preflight']['failures']}")
        self.last_load = report
        return report

    def query(self, sql: str) -> pd.DataFrame:
        """Executes a SQL query and returns a pandas DataFrame."""
        return self.conn.execute(sql).df()

    def get_table_counts(self) -> Dict[str, int]:
        """Row counts for every warehouse table (staging tables excluded)."""
        tables = [r[0] for r in self.conn.execute("SHOW TABLES").fetchall()]
        return {t: self.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                for t in sorted(tables) if not t.startswith("stg_")}
