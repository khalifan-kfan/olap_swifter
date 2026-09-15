"""
Tests for MedicalCube OLAP engine.
Verifies Slice, Dice, Rollup, and Pivot operations on Fact Constellation.
"""

import pytest
from olap_swifter.olap.store import MedicalOLAPStore
from olap_swifter.olap.cube import MedicalCube


def test_cube_slice_and_dice():
    store = MedicalOLAPStore()
    store.populate_synthetic_data(num_visits=150)
    cube = MedicalCube(conn=store.conn)

    # Slice: Filter by specific visit type
    slice_df = cube.slice_and_dice(
        measures=["COUNT(*) AS visit_count", "AVG(e.length_of_stay_days) AS avg_los"],
        group_by_dimensions=["f.facility_level"],
        filters={"v.visit_type": "Inpatient"},
        from_table="dim_visit v",
        joins=[
            "JOIN dim_facility f ON v.facility_id = f.facility_id",
            "JOIN fact_encounters e ON v.visit_id = e.visit_id"
        ]
    )
    assert len(slice_df) > 0
    assert "visit_count" in slice_df.columns
    assert "avg_los" in slice_df.columns

    # Dice: Filter across multiple dimensions
    dice_df = cube.slice_and_dice(
        measures=["COUNT(*) AS cnt", "SUM(e.encounter_cost_ugx) AS total_cost"],
        group_by_dimensions=["p.gender", "p.age_group"],
        filters={
            "v.visit_type": ["Inpatient", "Emergency"],
            "p.gender": "Female"
        },
        from_table="dim_visit v",
        joins=[
            "JOIN dim_patient p ON v.patient_id = p.patient_id",
            "JOIN fact_encounters e ON v.visit_id = e.visit_id"
        ]
    )
    assert len(dice_df) > 0
    assert (dice_df["gender"] == "Female").all()


def test_cube_rollup_and_pivot():
    store = MedicalOLAPStore()
    store.populate_synthetic_data(num_visits=150)
    cube = MedicalCube(conn=store.conn)

    # Rollup across region -> district
    rollup_df = cube.rollup(
        measure="COUNT(*)",
        hierarchy_levels=["f.region", "f.district"],
        from_table="dim_visit v",
        joins=["JOIN dim_facility f ON v.facility_id = f.facility_id"]
    )
    assert len(rollup_df) > 0
    # Rollup creates rows where region or district is NULL (subtotals & grand totals)
    assert rollup_df["measure_val"].sum() > 0

    # Pivot: Cross-tabulate visit_type against triage_urgency
    pivot_df = cube.pivot(
        row_dim="v.visit_type",
        col_dim="v.triage_urgency",
        from_table="dim_visit v"
    )
    assert len(pivot_df) > 0
    assert "visit_type" in pivot_df.columns
    assert len(pivot_df.columns) > 1
