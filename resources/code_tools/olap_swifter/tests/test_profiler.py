"""
Tests for JoinGraph, CubeSlicer and LatencyProfiler on the AMR schema:
join distances through the visit (encounter) spine, deep slicing, timing.
"""

from olap_swifter.olap.constellation import get_constellation_schema_metadata
from olap_swifter.olap.store import MedicalOLAPStore
from olap_swifter.profiler.join_graph import JoinGraph
from olap_swifter.profiler.latency import LatencyProfiler
from olap_swifter.profiler.slicer import CubeSlicer


def test_join_graph_distance_and_paths():
    jg = JoinGraph()
    jg.load_from_metadata(get_constellation_schema_metadata())  # the only registered schema
    assert jg.get_number_of_joins("fact_ast_results", "dim_organism") == 1  # linked directly
    assert jg.get_number_of_joins("fact_ast_results", "dim_facility") == 2  # through the spine
    assert jg.get_join_path("fact_ast_results", "dim_facility") == ["fact_ast_results", "dim_visit", "dim_facility"]
    sql, joins = jg.generate_join_sql("fact_ast_results", "dim_facility")
    assert joins == 2 and "JOIN dim_visit ON" in sql and "JOIN dim_facility ON" in sql


def test_deep_cube_slicer_and_latency():
    store = MedicalOLAPStore()
    store.populate_synthetic_data(num_visits=150)
    slicer = CubeSlicer(conn=store.conn)
    slices = slicer.dig_deepest_slices(
        base_from_clause="dim_visit v JOIN dim_patient p ON v.patient_id = p.patient_id "
                         "JOIN dim_facility f ON v.facility_id = f.facility_id",
        candidate_dimensions=["f.facility_level", "p.gender", "v.age_group"],
        min_depth=1, max_depth=3,
    )
    assert slices and slices[0].depth >= 2 and slices[0].non_empty_cells > 0

    prof = LatencyProfiler(conn=store.conn).profile_query("SELECT COUNT(*) FROM fact_encounters", label="count")
    assert prof.total_duration_ms > 0 and prof.row_count == 1
