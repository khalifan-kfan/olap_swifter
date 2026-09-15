"""
Query Latency and Table Component Profiler.
Measures query execution duration, component-level scan times, and comparison
across Star, Snowflake, and Constellation schema models.
"""

import time
import re
from typing import Dict, List, Any, Optional
import duckdb
import pandas as pd


class QueryLatencyProfile:
    def __init__(self, query_label: str, model_type: str, total_duration_ms: float, row_count: int, component_timings: Dict[str, Any], raw_explain: str):
        self.query_label = query_label
        self.model_type = model_type
        self.total_duration_ms = total_duration_ms
        self.row_count = row_count
        self.component_timings = component_timings
        self.raw_explain = raw_explain

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_label": self.query_label,
            "model_type": self.model_type,
            "total_duration_ms": round(self.total_duration_ms, 3),
            "row_count": self.row_count,
            "component_timings": self.component_timings
        }


class LatencyProfiler:
    """Measures query execution latency and table-level component timings."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self.conn = conn

    def profile_query(self, sql: str, label: str = "Query", model_type: str = "constellation", runs: int = 3) -> QueryLatencyProfile:
        """
        Executes a query multiple times to measure warm latency,
        and extracts execution plan details from EXPLAIN ANALYZE.
        """
        # Warmup and timing
        durations = []
        result_df = None
        for _ in range(runs):
            t0 = time.perf_counter()
            result_df = self.conn.execute(sql).df()
            t1 = time.perf_counter()
            durations.append((t1 - t0) * 1000.0)

        avg_duration_ms = sum(durations) / len(durations)
        row_count = len(result_df) if result_df is not None else 0

        # Component breakdown using EXPLAIN ANALYZE
        explain_sql = f"EXPLAIN ANALYZE {sql}"
        try:
            explain_df = self.conn.execute(explain_sql).df()
            raw_plan = "\n".join(explain_df.iloc[:, 1].tolist()) if explain_df.shape[1] > 1 else str(explain_df)
        except Exception:
            raw_plan = "EXPLAIN ANALYZE unavailable"

        # Parse component times from DuckDB explain plan
        component_timings = self._parse_components_from_plan(raw_plan)

        return QueryLatencyProfile(
            query_label=label,
            model_type=model_type,
            total_duration_ms=avg_duration_ms,
            row_count=row_count,
            component_timings=component_timings,
            raw_explain=raw_plan
        )

    def _parse_components_from_plan(self, plan: str) -> Dict[str, Any]:
        """Extracts scans, joins, and aggregates from execution plan."""
        components = {}
        # Look for Table scans (e.g. SCAN dim_visit, SCAN fact_encounters)
        scans = re.findall(r"(?:SEQ_SCAN|SCAN_TABLE|INDEX_SCAN)\s+\[([^\]]+)\]", plan)
        if scans:
            components["scanned_tables"] = list(set(scans))

        # Look for operators
        if "HASH_JOIN" in plan:
            components["has_hash_join"] = True
        if "PERFECT_HASH_GROUP_BY" in plan or "HASH_GROUP_BY" in plan:
            components["has_hash_aggregate"] = True

        # Extract timing lines if available in DuckDB profiling output
        timing_matches = re.findall(r"(\w+)\s+\(([\d\.]+)\s*(s|ms|us)\)", plan)
        if timing_matches:
            components["operators"] = [
                {"operator": op, "time": val, "unit": unit}
                for op, val, unit in timing_matches[:10]
            ]

        return components

    def compare_schema_models(self, star_sql: str, snowflake_sql: str, constellation_sql: str, label: str = "Benchmark") -> Dict[str, Any]:
        """Compares execution duration across Star, Snowflake, and Constellation equivalents."""
        p_star = self.profile_query(star_sql, label=f"{label} (Star)", model_type="star")
        p_snow = self.profile_query(snowflake_sql, label=f"{label} (Snowflake)", model_type="snowflake")
        p_const = self.profile_query(constellation_sql, label=f"{label} (Constellation)", model_type="constellation")

        return {
            "label": label,
            "star": p_star.to_dict(),
            "snowflake": p_snow.to_dict(),
            "constellation": p_const.to_dict(),
            "fastest": min([p_star, p_snow, p_const], key=lambda x: x.total_duration_ms).model_type
        }
