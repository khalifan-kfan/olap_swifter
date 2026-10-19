"""
Tests for the MedicalCube OLAP engine: slice, dice, rollup and pivot on a dummy
AMR warehouse (generated keys, so every join goes through them).
"""

from olap_swifter.olap.cube import MedicalCube
from olap_swifter.olap.store import MedicalOLAPStore


def _cube():
    store = MedicalOLAPStore()
    store.populate_synthetic_data(num_visits=150, schema_id="amr_surveillance")
    return MedicalCube(conn=store.conn)


def test_cube_slice_and_dice():
    cube = _cube()
    # Slice: resistant results only, by facility level
    slice_df = cube.slice_and_dice(
        measures=["COUNT(*) AS result_count", "AVG(a.mic_ug_ml) AS avg_mic"],
        group_by_dimensions=["f.facility_level"],
        filters={"a.ast_interpretation": "R"},
        from_table="fact_ast_results a",
        joins=["JOIN dim_visit v ON a.visit_id = v.visit_id",
               "JOIN dim_facility f ON v.facility_id = f.facility_id"],
    )
    assert len(slice_df) > 0 and {"result_count", "avg_mic"} <= set(slice_df.columns)

    # Dice: several dimensions filtered at once
    dice_df = cube.slice_and_dice(
        measures=["COUNT(*) AS cnt", "SUM(e.encounter_cost_ugx) AS total_cost"],
        group_by_dimensions=["p.gender", "v.age_group"],
        filters={"p.gender": "Female"},
        from_table="dim_visit v",
        joins=["JOIN dim_patient p ON v.patient_id = p.patient_id",
               "JOIN fact_encounters e ON v.visit_id = e.visit_id"],
    )
    assert len(dice_df) > 0 and (dice_df["gender"] == "Female").all()


def test_cube_rollup_and_pivot():
    cube = _cube()
    rollup_df = cube.rollup(
        measure="COUNT(*)",
        hierarchy_levels=["f.facility_level", "f.facility_name"],
        from_table="dim_visit v",
        joins=["JOIN dim_facility f ON v.facility_id = f.facility_id"],
    )
    # ROLLUP adds subtotal rows and a grand total equal to the visit count
    assert rollup_df["facility_level"].isna().any()
    grand = rollup_df[rollup_df["facility_level"].isna()]["measure_val"].iloc[0]
    assert grand == cube.conn.execute("SELECT COUNT(*) FROM dim_visit").fetchone()[0]

    pivot_df = cube.pivot(
        row_dim="v.age_group", col_dim="a.ast_interpretation",
        from_table="fact_ast_results a", joins=["JOIN dim_visit v ON a.visit_id = v.visit_id"],
    )
    assert len(pivot_df) > 0 and "age_group" in pivot_df.columns and len(pivot_df.columns) > 1
