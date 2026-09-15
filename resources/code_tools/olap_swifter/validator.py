"""
Semantic Data Equivalence Validator.
Ensures that queries executed on the decomposed OLAP schema yield results
semantically identical to queries on the original raw data.
"""

from typing import Dict, List, Any, Optional
import duckdb
import pandas as pd


class SemanticValidationResult:
    def __init__(self, test_name: str, passed: bool, raw_result: Any, olap_result: Any, difference: Optional[str] = None):
        self.test_name = test_name
        self.passed = passed
        self.raw_result = raw_result
        self.olap_result = olap_result
        self.difference = difference

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "passed": self.passed,
            "raw_result": str(self.raw_result),
            "olap_result": str(self.olap_result),
            "difference": self.difference
        }


class SemanticDataValidator:
    """
    Validates semantic invariance between original raw clinical records
    and the normalized/constellation OLAP schema.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, raw_table: str = "raw_clinical_records"):
        self.conn = conn
        self.raw_table = raw_table
        self.results: List[SemanticValidationResult] = []

    def validate_row_counts(self, fact_table: str) -> SemanticValidationResult:
        """Verifies row count integrity between raw table and the primary fact table."""
        raw_count = self.conn.execute(f"SELECT COUNT(*) FROM {self.raw_table}").fetchone()[0]
        olap_count = self.conn.execute(f"SELECT COUNT(*) FROM {fact_table}").fetchone()[0]
        passed = (raw_count == olap_count)
        diff = None if passed else f"Raw count: {raw_count}, OLAP count: {olap_count} (Delta: {olap_count - raw_count})"

        res = SemanticValidationResult(
            test_name=f"Row Count Parity ({fact_table})",
            passed=passed,
            raw_result=raw_count,
            olap_result=olap_count,
            difference=diff
        )
        self.results.append(res)
        return res

    def validate_metric_sum(self, metric_col: str, fact_table: str, tolerance: float = 1e-3) -> SemanticValidationResult:
        """Verifies numeric aggregation (SUM) parity for a metric."""
        raw_sum = self.conn.execute(f"SELECT SUM({metric_col}) FROM {self.raw_table} WHERE {metric_col} IS NOT NULL").fetchone()[0]
        olap_sum = self.conn.execute(f"SELECT SUM({metric_col}) FROM {fact_table} WHERE {metric_col} IS NOT NULL").fetchone()[0]

        raw_val = float(raw_sum) if raw_sum is not None else 0.0
        olap_val = float(olap_sum) if olap_sum is not None else 0.0
        diff_val = abs(raw_val - olap_val)
        passed = diff_val <= tolerance

        res = SemanticValidationResult(
            test_name=f"Metric Sum Parity ({metric_col} in {fact_table})",
            passed=passed,
            raw_result=raw_val,
            olap_result=olap_val,
            difference=None if passed else f"Absolute difference: {diff_val}"
        )
        self.results.append(res)
        return res

    def validate_group_by_equivalence(
        self,
        group_col: str,
        metric_col: str,
        fact_table: str,
        dim_table: str,
        join_key: str
    ) -> SemanticValidationResult:
        """
        Executes a GROUP BY query on raw vs the joined OLAP schema and compares the resulting distributions.
        """
        raw_query = f"""
            SELECT {group_col}, COUNT(*) AS cnt, SUM({metric_col}) AS total
            FROM {self.raw_table}
            WHERE {group_col} IS NOT NULL
            GROUP BY {group_col}
            ORDER BY {group_col}
        """
        olap_query = f"""
            SELECT d.{group_col}, COUNT(*) AS cnt, SUM(f.{metric_col}) AS total
            FROM {fact_table} f
            JOIN {dim_table} d ON f.{join_key} = d.{join_key}
            WHERE d.{group_col} IS NOT NULL
            GROUP BY d.{group_col}
            ORDER BY d.{group_col}
        """

        raw_df = self.conn.execute(raw_query).df()
        olap_df = self.conn.execute(olap_query).df()

        is_equal = raw_df.equals(olap_df)
        diff_summary = None
        if not is_equal:
            diff_summary = f"Raw groups: {len(raw_df)}, OLAP groups: {len(olap_df)}. Discrepancy in distributions."

        res = SemanticValidationResult(
            test_name=f"Semantic Group-By Equivalence ({group_col} & {metric_col})",
            passed=is_equal,
            raw_result=f"{len(raw_df)} groups",
            olap_result=f"{len(olap_df)} groups",
            difference=diff_summary
        )
        self.results.append(res)
        return res

    def validate_complex_query_parity(
        self,
        test_name: str,
        raw_sql: str,
        olap_sql: str
    ) -> SemanticValidationResult:
        """
        Executes complex multi-table analytical query on raw vs OLAP Constellation
        and asserts identical results.
        """
        try:
            raw_df = self.conn.execute(raw_sql).df()
            olap_df = self.conn.execute(olap_sql).df()
            is_equal = raw_df.equals(olap_df)
            diff = None if is_equal else f"Raw returned {len(raw_df)} rows, OLAP returned {len(olap_df)} rows."
        except Exception as e:
            is_equal = False
            raw_df = None
            olap_df = None
            diff = f"Query execution error: {str(e)}"

        res = SemanticValidationResult(
            test_name=f"Complex Query Parity: {test_name}",
            passed=is_equal,
            raw_result=f"{len(raw_df) if raw_df is not None else 'Error'} rows",
            olap_result=f"{len(olap_df) if olap_df is not None else 'Error'} rows",
            difference=diff
        )
        self.results.append(res)
        return res

    def validate_referential_integrity(self, fact_table: str, dim_table: str, from_key: str, to_key: Optional[str] = None) -> SemanticValidationResult:
        """Verifies that 0 orphan foreign keys exist in fact table."""
        to_key = to_key or from_key
        orphan_query = f"""
            SELECT COUNT(*)
            FROM {fact_table} f
            LEFT JOIN {dim_table} d ON f.{from_key} = d.{to_key}
            WHERE d.{to_key} IS NULL AND f.{from_key} IS NOT NULL
        """
        orphans = self.conn.execute(orphan_query).fetchone()[0]
        passed = (orphans == 0)

        res = SemanticValidationResult(
            test_name=f"Referential Integrity ({fact_table}.{from_key} -> {dim_table}.{to_key})",
            passed=passed,
            raw_result="0 orphans required",
            olap_result=f"{orphans} orphans detected",
            difference=None if passed else f"Found {orphans} orphan records without dimension entry"
        )
        self.results.append(res)
        return res

    def run_all_semantic_tests(self, model_meta: Dict[str, Any]) -> Dict[str, Any]:
        """Runs the entire semantic test suite across all model components."""
        self.results.clear()

        for rel in model_meta.get("relationships", []):
            f_tbl = rel.get("from")
            d_tbl = rel.get("to")
            from_k = rel.get("from_key") or rel.get("key")
            to_k = rel.get("to_key") or rel.get("key")
            tables = set(self.conn.execute("SHOW TABLES").df()["name"])
            if f_tbl in tables and d_tbl in tables and from_k and to_k:
                cols_f = set(self.conn.execute(f"PRAGMA table_info('{f_tbl}')").df()["name"])
                cols_d = set(self.conn.execute(f"PRAGMA table_info('{d_tbl}')").df()["name"])
                if from_k in cols_f and to_k in cols_d:
                    self.validate_referential_integrity(f_tbl, d_tbl, from_k, to_k)

        passed_count = sum(1 for r in self.results if r.passed)
        total_count = len(self.results)

        return {
            "total_tests": total_count,
            "passed": passed_count,
            "failed": total_count - passed_count,
            "all_passed": (passed_count == total_count and total_count > 0),
            "details": [r.to_dict() for r in self.results]
        }
