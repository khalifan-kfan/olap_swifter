"""
Tests for DynamicOLAPBuilder and SemanticDataValidator.
Verifies zero variable loss, field switching, and semantic query equivalence.
"""

import pytest
import pandas as pd
import duckdb

from olap_swifter.builder import DynamicOLAPBuilder
from olap_swifter.validator import SemanticDataValidator
from olap_swifter.olap.store import MedicalOLAPStore


def test_zero_variable_loss_and_field_switching():
    store = MedicalOLAPStore()
    store.populate_synthetic_data(num_visits=100)
    builder = DynamicOLAPBuilder(conn=store.conn)

    # Get sample raw records
    raw_df = store.conn.execute("SELECT * FROM dim_patient LIMIT 50").df()
    builder.register_raw_data(raw_df, table_name="test_raw_patients")

    # 1. Verify zero variable loss
    all_mapped, missing = builder.verify_no_variable_left_out()
    assert all_mapped is True
    assert len(missing) == 0

    # 2. Test field switching
    builder.switch_field("gender", "dim_patient_demographics", new_role="dimension_attribute")
    inv = {f["name"]: f for f in builder.get_field_inventory()}
    assert inv["gender"]["target_table"] == "dim_patient_demographics"


def test_semantic_data_equivalence():
    store = MedicalOLAPStore()
    store.populate_synthetic_data(num_visits=150)
    validator = SemanticDataValidator(conn=store.conn, raw_table="dim_visit")

    # Validate row counts
    count_res = validator.validate_row_counts("dim_visit")
    assert count_res.passed is True

    # Validate referential integrity between visit and patient
    ref_res = validator.validate_referential_integrity("dim_visit", "dim_patient", "patient_id")
    assert ref_res.passed is True
