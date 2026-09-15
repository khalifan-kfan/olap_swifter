"""
Tests for JoinGraph, CubeSlicer, and LatencyProfiler.
Verifies join distance calculation, deep cube slicing, and execution benchmarking.
"""

import pytest
from olap_swifter.profiler.join_graph import JoinGraph
from olap_swifter.profiler.slicer import CubeSlicer
from olap_swifter.profiler.latency import LatencyProfiler
from olap_swifter.olap.store import MedicalOLAPStore
from olap_swifter.olap.constellation import get_constellation_schema_metadata


def test_join_graph_distance_and_paths():
    jg = JoinGraph()
    meta = get_constellation_schema_metadata()
    jg.load_from_metadata(meta)

    # 1. Distance from fact_prescriptions to dim_medication (direct join = 1)
    d1 = jg.get_number_of_joins("fact_prescriptions", "dim_medication")
    assert d1 == 1

    # 2. Distance from fact_prescriptions to dim_facility (via dim_visit = 2 joins)
    d2 = jg.get_number_of_joins("fact_prescriptions", "dim_facility")
    assert d2 == 2
    path = jg.get_join_path("fact_prescriptions", "dim_facility")
    assert path == ["fact_prescriptions", "dim_visit", "dim_facility"]

    # 3. SQL generation
    sql, num_j = jg.generate_join_sql("fact_prescriptions", "dim_facility")
    assert num_j == 2
    assert "JOIN dim_visit ON" in sql
    assert "JOIN dim_facility ON" in sql


def test_deep_cube_slicer_and_latency():
    store = MedicalOLAPStore()
    store.populate_synthetic_data(num_visits=150)

    slicer = CubeSlicer(conn=store.conn)
    base_from = "dim_visit v JOIN dim_patient p ON v.patient_id = p.patient_id JOIN dim_facility f ON v.facility_id = f.facility_id"
    candidate_dims = ["f.facility_level", "p.gender", "p.age_group", "v.visit_type"]

    slices = slicer.dig_deepest_slices(
        base_from_clause=base_from,
        candidate_dimensions=candidate_dims,
        min_depth=1,
        max_depth=3
    )
    assert len(slices) > 0
    deepest = slices[0]
    assert deepest.depth >= 2
    assert deepest.non_empty_cells > 0

    # Test Latency Profiler
    profiler = LatencyProfiler(conn=store.conn)
    prof = profiler.profile_query("SELECT COUNT(*) FROM fact_encounters", label="Test Count")
    assert prof.total_duration_ms > 0
    assert prof.row_count == 1
