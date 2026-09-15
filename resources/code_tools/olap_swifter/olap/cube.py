"""
Multi-Dimensional Medical OLAP Cube Engine.
Provides core OLAP operations: Slice, Dice, Rollup, Drilldown, and Pivot
over the Fact Constellation schema with the dim_visit spine.
"""

from typing import Dict, List, Any, Optional
import duckdb
import pandas as pd


class MedicalCube:
    """
    High-performance OLAP Cube abstraction over DuckDB Fact Constellation.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn

    def slice_and_dice(
        self,
        measures: List[str],
        group_by_dimensions: List[str],
        filters: Optional[Dict[str, Any]] = None,
        from_table: str = "fact_encounters",
        joins: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Executes a Slice (fixing a dimension) or Dice (bounding multiple dimensions)
        and aggregates specified measures across the dimensions.
        """
        select_clause = ", ".join(group_by_dimensions + measures)
        group_clause = ", ".join(group_by_dimensions) if group_by_dimensions else "1"

        join_clause = "\n".join(joins) if joins else ""

        where_clauses = []
        if filters:
            for col, val in filters.items():
                if isinstance(val, (list, tuple)):
                    formatted_vals = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in val)
                    where_clauses.append(f"{col} IN ({formatted_vals})")
                elif isinstance(val, str):
                    where_clauses.append(f"{col} = '{val}'")
                else:
                    where_clauses.append(f"{col} = {val}")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
            SELECT {select_clause}
            FROM {from_table}
            {join_clause}
            {where_sql}
            GROUP BY {group_clause}
            ORDER BY {group_clause}
        """
        return self.conn.execute(query).df()

    def rollup(
        self,
        measure: str,
        hierarchy_levels: List[str],
        from_table: str = "fact_encounters",
        joins: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Performs multi-level hierarchical ROLLUP across a dimensional hierarchy
        (e.g., region -> district -> facility_level).
        """
        join_clause = "\n".join(joins) if joins else ""
        levels_sql = ", ".join(hierarchy_levels)

        query = f"""
            SELECT {levels_sql}, {measure} AS measure_val
            FROM {from_table}
            {join_clause}
            GROUP BY ROLLUP ({levels_sql})
            ORDER BY {levels_sql}
        """
        return self.conn.execute(query).df()

    def pivot(
        self,
        row_dim: str,
        col_dim: str,
        measure_sql: str = "COUNT(*)",
        from_table: str = "fact_encounters",
        joins: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Generates a cross-tabulated Pivot table between two dimensions.
        """
        join_clause = "\n".join(joins) if joins else ""
        # Get distinct values for col_dim
        col_vals_query = f"SELECT DISTINCT {col_dim} FROM {from_table} {join_clause} WHERE {col_dim} IS NOT NULL ORDER BY {col_dim}"
        col_vals = [r[0] for r in self.conn.execute(col_vals_query).fetchall()]

        pivot_cols = []
        for val in col_vals:
            safe_val = str(val).replace("'", "''")
            alias = str(val).replace(" ", "_").replace("-", "_").lower()
            pivot_cols.append(f"SUM(CASE WHEN {col_dim} = '{safe_val}' THEN 1 ELSE 0 END) AS {alias}_count")

        query = f"""
            SELECT {row_dim}, {', '.join(pivot_cols)}
            FROM {from_table}
            {join_clause}
            WHERE {row_dim} IS NOT NULL
            GROUP BY {row_dim}
            ORDER BY {row_dim}
        """
        return self.conn.execute(query).df()
