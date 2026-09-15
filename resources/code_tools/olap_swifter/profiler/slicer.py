"""
Deep Cube Slicer and Granularity Explorer.
Discovers and digs out the deepest non-empty slices on any OLAP cube.
"""

from typing import Dict, List, Any, Optional, Tuple
import itertools
import duckdb
import pandas as pd


class DeepSliceResult:
    def __init__(self, dimensions: List[str], depth: int, non_empty_cells: int, total_volume: int, min_cell_size: int, max_cell_size: int, avg_cell_size: float, query_sql: str):
        self.dimensions = dimensions
        self.depth = depth
        self.non_empty_cells = non_empty_cells
        self.total_volume = total_volume
        self.min_cell_size = min_cell_size
        self.max_cell_size = max_cell_size
        self.avg_cell_size = avg_cell_size
        self.query_sql = query_sql

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimensions": self.dimensions,
            "depth": self.depth,
            "non_empty_cells": self.non_empty_cells,
            "total_volume": self.total_volume,
            "min_cell_size": self.min_cell_size,
            "max_cell_size": self.max_cell_size,
            "avg_cell_size": round(self.avg_cell_size, 2),
            "query_sql": self.query_sql
        }


class CubeSlicer:
    """Explores multi-dimensional combinations to find the deepest and most informative slices."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn

    def dig_deepest_slices(
        self,
        base_from_clause: str,
        candidate_dimensions: List[str],
        metric_sql: str = "COUNT(*)",
        min_depth: int = 1,
        max_depth: int = 5,
        min_cell_threshold: int = 1
    ) -> List[DeepSliceResult]:
        """
        Recursively explores dimension combinations up to max_depth,
        finding which multi-dimensional intersections produce valid, populated cube slices.
        """
        results: List[DeepSliceResult] = []
        max_depth = min(max_depth, len(candidate_dimensions))

        for depth in range(min_depth, max_depth + 1):
            for dim_combo in itertools.combinations(candidate_dimensions, depth):
                dim_list = list(dim_combo)
                select_dims = ", ".join(f"{d}" for d in dim_list)
                group_by_dims = ", ".join(f"{d}" for d in dim_list)

                cube_query = f"""
                    SELECT {select_dims}, {metric_sql} AS cell_count
                    FROM {base_from_clause}
                    WHERE {" AND ".join(f"{d} IS NOT NULL" for d in dim_list)}
                    GROUP BY {group_by_dims}
                    HAVING {metric_sql} >= {min_cell_threshold}
                """

                try:
                    stats_query = f"""
                        WITH cube_slice AS (
                            {cube_query}
                        )
                        SELECT
                            COUNT(*) AS non_empty_cells,
                            COALESCE(SUM(cell_count), 0) AS total_vol,
                            COALESCE(MIN(cell_count), 0) AS min_size,
                            COALESCE(MAX(cell_count), 0) AS max_size,
                            COALESCE(AVG(cell_count), 0) AS avg_size
                        FROM cube_slice
                    """
                    stats = self.conn.execute(stats_query).fetchone()
                    if stats and stats[0] > 0:
                        res = DeepSliceResult(
                            dimensions=dim_list,
                            depth=depth,
                            non_empty_cells=stats[0],
                            total_volume=stats[1],
                            min_cell_size=stats[2],
                            max_cell_size=stats[3],
                            avg_cell_size=stats[4],
                            query_sql=cube_query
                        )
                        results.append(res)
                except Exception as e:
                    # Skip invalid cross-joins or ambiguous column errors
                    continue

        # Sort by depth descending, then by non_empty_cells descending
        results.sort(key=lambda r: (r.depth, r.non_empty_cells), reverse=True)
        return results

    def get_slice_preview(self, slice_result: DeepSliceResult, limit: int = 10) -> pd.DataFrame:
        """Returns top rows for a given deep slice."""
        preview_sql = f"{slice_result.query_sql} ORDER BY cell_count DESC LIMIT {limit}"
        return self.conn.execute(preview_sql).df()
